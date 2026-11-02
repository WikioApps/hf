import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import time
from weakref import WeakValueDictionary
from urllib.parse import urlparse, quote
from bot.core.settings import destination
from bot.helper.ext_utils.log_utils import redact, register_secrets
from bot.helper.ext_utils.files_utils import filename, safe_path, check_space
from bot.helper.ext_utils.process_utils import run_worker
from bot.helper.mirror_leech_utils.status_utils.task_status import TaskStatus, TERMINAL
from bot.helper.mirror_leech_utils.download_utils import aria2_download, telegram_download
from bot.helper.mirror_leech_utils.download_utils.direct_link_generator import resolve


class Slots:
    def __init__(self, limit):
        self.limit = limit
        self.running = 0
        self.condition = asyncio.Condition()

    @asynccontextmanager
    async def enter(self):
        async with self.condition:
            await self.condition.wait_for(lambda: self.running < self.limit())
            self.running += 1
        try:
            yield
        finally:
            async with self.condition:
                self.running -= 1
                self.condition.notify_all()


class TaskManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self.db = runtime.db
        self.tasks = {}
        self.downloads = Slots(lambda: runtime.settings['download_slots'])
        self.uploads = Slots(lambda: runtime.settings['upload_slots'])
        self.extraction = asyncio.Semaphore(1)
        self.paths = WeakValueDictionary()
        self.save_locks = WeakValueDictionary()
        self.closing = False
        self.selection_events = {}
        self.selection_limit = 3600
        self.submissions = asyncio.Lock()

    def active(self, uid=None, chat=None):
        return [t for t in self.tasks.values() if t.phase not in TERMINAL
                and (uid is None or t.user_id == uid) and (chat is None or t.chat_id == chat)]

    async def save(self, task):
        lock = self.save_locks.setdefault(task.id,asyncio.Lock())
        async with lock:
            await self.db.save_task(task.document(self.db))

    async def add(self, uid, chat, mid, source, options):
        async with self.submissions:
            return await self._add(uid,chat,mid,source,options)

    async def _add(self, uid, chat, mid, source, options):
        if self.closing or len(self.active()) >= self.runtime.settings['max_tasks']:
            raise ValueError('The task limit has been reached. Try again after a transfer finishes.')
        profile = await self.db.user(uid)
        dest = destination(profile)
        register_secrets(profile.get('token'),options.get('password'))
        processing = options.get('processing',profile['processing'])
        options = {**options,'processing':processing}
        task = TaskStatus(uid,chat,mid,source,dest,options,self.runtime.config.data)
        task.directory = self.runtime.config.data/'tasks'/task.id
        task.directory.mkdir(parents=True,exist_ok=True)
        task.name = filename(options.get('name') or source.get('name') or 'download.bin')
        await self.save(task)
        self.runtime.logger.info('Task accepted task=%s user=%s source=%s file=%s',task.id,uid,source['kind'],task.name)
        self.tasks[task.id] = task
        task.runner = asyncio.create_task(self.run(task),name='transfer-'+task.id)
        return task

    async def recover(self):
        async for data in self.db.db.tasks.find({'bot':self.db.scope,'phase':{'$nin':list(TERMINAL)}}):
            await self.db.db.tasks.update_one({'_id':data['_id']},{'$set':{
                'phase':'Interrupted','error':'Host restarted. Use /retry to resume or re-download.'}})

    async def find(self, tid, uid):
        task = self.tasks.get(tid)
        data = task or await self.db.task(tid)
        owner = task.user_id if task else data.get('user_id') if data else None
        if owner is None or uid not in (owner,self.runtime.config.owner_id):
            raise ValueError('Task not found or not yours.')
        return data

    async def retry(self, tid, uid):
        async with self.submissions:
            return await self._retry(tid,uid)

    async def _retry(self, tid, uid):
        data = await self.find(tid,uid)
        if isinstance(data,TaskStatus):
            if data.runner and not data.runner.done():
                raise ValueError('The previous attempt is still stopping. Try again shortly.')
            data = data.document(self.db)
        if data['phase'] not in ('Failed','Cancelled','Interrupted'):
            raise ValueError('Only failed, cancelled or interrupted tasks can be retried.')
        if len(self.active()) >= self.runtime.settings['max_tasks']:
            raise ValueError('The task limit has been reached.')
        source = json.loads(self.db.open(data['source']))
        keys = ('user_id','chat_id','message_id','destination','options','name','created','downloaded','results','members','selected')
        task = TaskStatus(**{k:data[k] for k in keys},source=source,id=tid,
                          directory=self.runtime.config.data/'tasks'/tid)
        task.directory.mkdir(parents=True,exist_ok=True)
        # Passwords are sealed separately so MongoDB never holds them as plaintext.
        if task.options.get('password'):
            task.options['password'] = self.db.open(task.options['password'])
        self.tasks[tid] = task
        task.runner = asyncio.create_task(self.run(task))
        return task

    async def cancel(self, tid, uid):
        task = await self.find(tid,uid)
        if not isinstance(task,TaskStatus) or not task.runner or task.runner.done():
            raise ValueError('The task is not running.')
        task.runner.cancel()
        await asyncio.gather(task.runner,return_exceptions=True)

    async def run(self, task):
        try:
            await self.download(task)
            mode = task.options['processing']
            if mode == 'extract':
                await self.extract(task)
            elif mode in ('zip','7z'):
                await self.compress(task,mode)
            else:
                await self.upload(task,task.directory/'source',task.name)
            self.runtime.logger.info('Task complete task=%s files=%s file=%s',task.id,len(task.results),task.name)
            task.phase = 'Complete'
            await self.save(task)
            await self.runtime.completed(task)
            self.clean_local(task)
        except asyncio.CancelledError:
            self.runtime.logger.info('Task stopped task=%s phase=%s',task.id,task.phase)
            task.phase = 'Interrupted' if self.closing else 'Cancelled'
            task.error = 'Transfer stopped. Retained files can be retried or cleaned.'
            await self.finish_error(task)
        except Exception as error:
            self.runtime.logger.exception('Task failed task=%s phase=%s file=%s',task.id,task.phase,task.name)
            task.phase = 'Failed'
            task.error = redact(str(error)) if type(error) is ValueError else type(error).__name__+': transfer failed. Details were saved for /logs.'
            await self.finish_error(task)
        finally:
            self.selection_events.pop(task.id, None)
            await self.runtime.status.refresh_all()
            if task.phase in TERMINAL:
                self.tasks.pop(task.id, None)

    async def finish_error(self, task):
        try:
            await self.save(task)
            if not self.closing:
                await self.runtime.failed(task)
        except Exception:
            self.runtime.logger.error('Could not save or send task failure',extra={'task':task.id})

    async def download(self, task):
        source_file = task.directory/'source'
        if task.downloaded and source_file.exists():
            return
        task.downloaded = False
        task.stage('QueueDl')
        async with self.downloads.enter():
            if task.source['kind'] == 'telegram':
                await telegram_download.download(task,self.runtime.client,self.runtime.settings)
            else:
                task.stage('Resolve')
                info = await resolve(task,task.source['url'])
                if not task.options.get('name') and info.get('name'):
                    task.name = filename(info['name'])
                if info['engine'] == 'gdown':
                    task.stage('Download'); task.engine = 'Google Drive'
                    await run_worker(task,'drive',[info['url'],source_file,self.max_file])
                else:
                    await aria2_download.download(task,info,self.runtime.settings)
            if not source_file.is_file():
                raise ValueError('The download engine did not create the expected local file. See /logs for details.')
            self.runtime.logger.info('Download verified task=%s bytes=%s file=%s',task.id,source_file.stat().st_size,task.name)
            task.downloaded = True
            await self.save(task)

    @property
    def max_file(self):
        return self.runtime.settings['max_file_gb']*1024**3

    async def upload(self, task, path, name):
        if any(item['source_name'] == name for item in task.results):
            return
        prefix = task.options.get('path',task.destination['path'])
        remote = '/'.join(p for p in (prefix,safe_path(name)) if p)
        lock_key = (task.destination['namespace'],task.destination['repo_name'],task.destination['repo_type'],task.destination['branch'],remote)
        lock = self.paths.setdefault(lock_key,asyncio.Lock())
        task.stage('QueueUp',path.stat().st_size)
        async with self.uploads.enter(),lock:
            profile = await self.db.user(task.user_id)
            if not profile['token']:
                raise ValueError('HF token is missing. Set it in /usettings, then retry.')
            task.engine = 'Hugging Face'
            result = await run_worker(task,'upload',[path,remote],secret={
                'token':profile['token'],'destination':task.destination})
            self.runtime.logger.info('HF upload confirmed task=%s path=%s bytes=%s skipped=%s',task.id,result.get('path'),result.get('size'),result.get('skipped',False))
            task.results.append({**result,'source_name':name})
            await self.save(task)

    async def archive_path(self, task):
        folder = task.directory/'archive'
        folder.mkdir(exist_ok=True)
        archive = folder/filename(task.name)
        if not archive.exists():
            archive.hardlink_to(task.directory/'source')
        return archive

    async def extract(self, task):
        archive = await self.archive_path(task)
        if not task.members:
            task.stage('Inspect')
            task.members = await run_worker(task,'list',[archive],secret={
                'password':task.options.get('password',''),'max_files':self.runtime.settings['archive_files'],
                'max_bytes':self.runtime.settings['archive_gb']*1024**3},timeout=120)
            task.selected = list(range(len(task.members)))
            await self.save(task)
        if task.options.get('select'):
            task.stage('Select')
            await self.save(task)
            event = self.selection_events.setdefault(task.id,asyncio.Event())
            await self.runtime.offer_selection(task)
            await asyncio.wait_for(event.wait(),self.selection_limit)
            self.selection_events.pop(task.id,None)
            task.options['select'] = False
            await self.save(task)
        for index in task.selected:
            member = task.members[index]
            task.current = member['name']
            if any(r['source_name'] == member['name'] for r in task.results):
                continue
            if member['size'] > self.max_file:
                raise ValueError('An archive member exceeds the file-size limit.')
            output = task.directory/'member.tmp'
            async with self.extraction:
                check_space(task.directory,member['size'])
                task.stage('Extract',member['size']); task.engine = 'Archive'
                await run_worker(task,'extract',[archive,member['original'],output,self.max_file],
                                 secret={'password':task.options.get('password','')})
            await self.upload(task,output,member['name'])
            output.unlink(missing_ok=True)

    async def compress(self, task, kind):
        source = await self.archive_path(task)
        output = task.directory/('packed.'+kind)
        async with self.extraction:
            check_space(task.directory,(task.directory/'source').stat().st_size)
            task.stage('Archive'); task.engine = '7-Zip'
            await run_worker(task,'compress',[source,output,kind,self.max_file])
        await self.upload(task,output,task.name+'.'+kind)

    def clean_local(self, task):
        shutil.rmtree(task.directory,ignore_errors=True)

    async def clean(self, tid, uid):
        data = await self.find(tid,uid)
        phase = data.phase if isinstance(data,TaskStatus) else data['phase']
        if phase not in TERMINAL:
            raise ValueError('Cancel the task before cleaning local files.')
        shutil.rmtree(self.runtime.config.data/'tasks'/tid,ignore_errors=True)

    async def close(self):
        self.closing = True
        pending = [task.runner for task in self.active() if task.runner]
        for runner in pending:
            runner.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
