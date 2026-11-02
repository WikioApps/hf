"""Task registry, independent download/upload queues and durable retry manifests."""
import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shutil
import time
import uuid
from urllib.parse import quote
from config import filename, safe_path
from transfers import download, worker, check_space

LOG = logging.getLogger('hf.tasks')
TERMINAL = {'Complete', 'Failed', 'Cancelled', 'Interrupted'}


def size(n):
    if n is None or n < 0: return '?'
    for unit in ('B','KB','MB','GB','TB'):
        if n < 1024: return f'{n:.2f}{unit}'
        n /= 1024
    return f'{n:.2f}PB'


def duration(n):
    if n is None: return '-'
    n = max(0, int(n)); out = ''
    for label, span in [('d',86400),('h',3600),('m',60),('s',1)]:
        v, n = divmod(n, span)
        if v: out += f'{v}{label}'
    return out or '0s'


@dataclass
class Job:
    id: str
    user_id: int
    chat_id: int
    message_id: int
    source: object
    directory: Path
    name: str = 'download.bin'
    rename: bool = False
    extract: bool = False
    select: bool = False
    prefix: str = ''
    phase: str = 'QueueDl'
    engine: str = 'Python'
    total: int = 0
    done: int = 0
    created: float = field(default_factory=time.time)
    phase_start: float = field(default_factory=time.monotonic)
    error: str = ''
    downloaded: bool = False
    uploaded: dict = field(default_factory=dict)
    members: list = field(default_factory=list)
    selected: set = field(default_factory=set)
    selection: asyncio.Event = field(default_factory=asyncio.Event)
    process: object = None
    runner: object = None
    current: str = ''
    count: int = 0
    def stage(self, phase, total=0):
        self.phase = phase; self.total = total; self.done = 0; self.phase_start = time.monotonic()
    def advance(self, done): self.done = done
    @property
    def speed(self): return self.done / max(time.monotonic() - self.phase_start, .001)
    @property
    def eta(self): return (self.total-self.done)/self.speed if self.total and self.speed else None
    def save(self):
        data = {k:getattr(self,k) for k in ('id','user_id','chat_id','message_id','name','rename','extract','select','prefix','phase','created','error','downloaded','uploaded','members')}
        # Store source messages by location; never serialize Telegram client/session objects.
        data['source'] = self.source if isinstance(self.source, (str,dict)) else {'chat': self.source.chat_id, 'message': self.source.id}
        data['selected'] = sorted(self.selected)
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.directory / 'manifest.tmp'
        temp.write_text(json.dumps(data), encoding='utf-8'); temp.replace(self.directory / 'manifest.json')


class Manager:
    def __init__(self, cfg, client, user_client=None, notify=None):
        self.cfg, self.client, self.user_client = cfg, client, user_client
        self.notify = notify
        self.jobs = {}
        self.dl = asyncio.Semaphore(cfg.downloads)
        self.up = asyncio.Semaphore(cfg.uploads)
        self.ex = asyncio.Semaphore(1)
        self.closing = False
        cfg.data.mkdir(parents=True, exist_ok=True)
        for file in (cfg.data / 'tasks').glob('*/manifest.json'):
            try:
                data = json.loads(file.read_text()); data['selected'] = set(data.get('selected', []))
                job = Job(directory=file.parent, **data)
                if job.phase not in TERMINAL:
                    job.phase = 'Interrupted'; job.error = 'Service restarted; use /retry ' + job.id
                self.jobs[job.id] = job; job.save()
            except (ValueError, TypeError, KeyError): LOG.error('invalid_manifest', extra={'task':file.parent.name})
    def active(self): return [j for j in self.jobs.values() if j.phase not in TERMINAL]
    def add(self, user_id, chat_id, message_id, source, **options):
        if self.closing: raise ValueError('Service is shutting down')
        if len(self.active()) >= self.cfg.max_tasks: raise ValueError('Task queue is full')
        ident = uuid.uuid4().hex[:12]
        job = Job(ident, user_id, chat_id, message_id, source, self.cfg.data / 'tasks' / ident, **options)
        job.save(); self.jobs[ident] = job
        job.runner = asyncio.create_task(self.run(job), name='task-' + ident)
        return job
    async def send(self, job, text):
        if self.notify:
            try: await self.notify(job, text)
            except Exception: LOG.warning('notification_failed', extra={'task': job.id})
    async def retry(self, job):
        if job.phase not in ('Failed','Cancelled','Interrupted'): raise ValueError('Only failed, cancelled or interrupted tasks can be retried')
        if len(self.active()) >= self.cfg.max_tasks: raise ValueError('Task queue is full')
        job.error = ''; job.stage('QueueDl'); job.save()
        job.runner = asyncio.create_task(self.run(job))
    async def run(self, job):
        try:
            source = job.directory / 'source'
            if not (job.downloaded and source.exists()):
                job.stage('QueueDl')
                async with self.dl:
                    if isinstance(job.source, dict):
                        job.source = await self.client.get_messages(job.source['chat'], ids=job.source['message'])
                        if not job.source: raise ValueError('Original Telegram message is unavailable; send it again')
                    await download(job, self.cfg, self.client, self.user_client)
                    job.downloaded = True; job.save()
            if job.extract:
                if job.name.lower().endswith('.rar'): raise ValueError('RAR extraction requires an external decoder; upload intact or use ZIP/TAR/7Z')
                if not job.members:
                    job.stage('Inspect'); job.engine = 'Python archives'
                    # Use a suffix-bearing link; the original downloaded payload remains intact.
                    (job.directory / 'archive').mkdir(exist_ok=True)
                    archive = job.directory / 'archive' / filename(job.name)
                    if not archive.exists(): archive.hardlink_to(source)
                    job.members = await worker(job, 'list', archive, cfg=self.cfg)
                    if not job.members: raise ValueError('Archive contains no regular files')
                    if len(job.members) > self.cfg.max_members or sum(s for _,s in job.members) > self.cfg.max_extract:
                        raise ValueError('Archive exceeds configured member count or expanded-size limit')
                    names = [n for n,_ in job.members]
                    if len(names) != len(set(names)): raise ValueError('Archive contains duplicate paths')
                    job.selected = set(range(len(job.members))); job.save()
                else:
                    (job.directory / 'archive').mkdir(exist_ok=True)
                    archive = job.directory / 'archive' / filename(job.name)
                if job.select and not job.uploaded:
                    job.stage('Select'); job.save()
                    await self.send(job, 'Select archive members with /select ' + job.id + '\nSelection expires in one hour.')
                    await asyncio.wait_for(job.selection.wait(), 3600)
                    if not job.selected: raise ValueError('No archive members selected')
                for index in sorted(job.selected):
                    name, length = job.members[index]
                    job.current = name; job.count = index + 1
                    if name in job.uploaded: continue
                    target = job.directory / 'member.tmp'
                    async with self.ex:
                        check_space(self.cfg, length)
                        if length > self.cfg.max_bytes: raise ValueError('Archive member exceeds MAX_FILE_GB')
                        job.engine = 'Python archives'; job.stage('Extract', length)
                        await worker(job, 'extract', archive, name, target, self.cfg.max_bytes, cfg=self.cfg)
                    await self.upload(job, target, name)
                    target.unlink(missing_ok=True)
            else:
                await self.upload(job, source, job.name)
            job.stage('Complete'); job.save()
            root = self.repo_url(job)
            await self.send(job, f'✅ Upload complete\nTask: {job.id}\nFiles: {len(job.uploaded)}\nElapsed: {duration(time.time()-job.created)}\n{root}')
            # Keep only the manifest for history. Never remove inputs before all commits succeed.
            for item in job.directory.iterdir():
                if item.name != 'manifest.json':
                    if item.is_dir(): shutil.rmtree(item)
                    else: item.unlink()
        except asyncio.CancelledError:
            job.phase = 'Interrupted' if self.closing else 'Cancelled'
            job.error = 'Files retained; retry or clean this task'; job.save()
            if not self.closing: await self.send(job, f'⏹ {job.phase}: {job.id}. Use /retry or /clean with this ID.')
        except Exception as exc:
            job.phase = 'Failed'
            # Our validation messages are safe; third-party HTTP/RPC errors can include signed URLs.
            job.error = str(exc) if type(exc) is ValueError else type(exc).__name__ + ': transfer failed'
            job.save(); LOG.warning('task_failed', extra={'task':job.id,'error_type':type(exc).__name__})
            await self.send(job, f'❌ Task {job.id}: {job.error}\nFiles retained. /retry {job.id}')
    async def upload(self, job, path, name):
        if name in job.uploaded: return
        # Stable per-task namespace prevents concurrent files silently overwriting each other.
        parts = [self.cfg.prefix, job.prefix, job.id, safe_path(name)]
        remote = '/'.join(p for p in parts if p)
        job.stage('QueueUp', path.stat().st_size); job.save()
        async with self.up:
            job.engine = 'Hugging Face / Python LFS'; job.stage('Prepare', path.stat().st_size)
            info = await worker(job, 'upload', path, remote, cfg=self.cfg)
            if not info or not info.get('commit'): raise ValueError('Hugging Face commit was not confirmed')
            job.uploaded[name] = info; job.save()
    def repo_url(self, job):
        kind = 'datasets/' if self.cfg.repo_type == 'dataset' else ''
        path = '/'.join(p for p in [self.cfg.prefix, job.prefix, job.id] if p)
        return f'https://huggingface.co/{kind}{self.cfg.repo_id}/tree/{quote(self.cfg.branch,safe="")}/{quote(path,safe="/")}'
    async def cancel(self, job):
        if job.runner and not job.runner.done():
            job.runner.cancel(); await job.runner
    async def close(self):
        self.closing = True
        tasks = [j.runner for j in self.active() if j.runner]
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
