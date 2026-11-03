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
from bot.helper.mirror_leech_utils.download_utils.direct_link_generator import resolve, refresh_item
from bot.helper.ext_utils.resource_manager import ResourceManager


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
        self.result_locks = WeakValueDictionary()
        self.resources = ResourceManager(runtime.config.data,lambda:runtime.settings)

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
        task.resource_manager = self.resources
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
        task.resource_manager = self.resources
        task.collection = data.get('collection',False)
        task.file_count = data.get('file_count',0)
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
            await self.transfer(task)
            self.runtime.logger.info('Task complete task=%s files=%s file=%s',task.id,len(task.results),task.name)
            task.lanes.clear()
            task.phase = 'Complete'
            await self.save(task)
            self.clean_local(task)
            try:
                await self.runtime.completed(task)
            except Exception:
                self.runtime.logger.exception('Completion message could not be sent task=%s',task.id)
        except asyncio.CancelledError:
            task.phase = 'Interrupted' if self.closing else 'Cancelled'
            task.error = 'Transfer stopped. Confirmed uploads are kept; remaining files can be retried.'
            await self.finish_error(task)
        except Exception as error:
            self.runtime.logger.exception('Task failed task=%s phase=%s file=%s',task.id,task.phase,task.name)
            task.phase = 'Failed'
            task.error = redact(str(error)) if type(error) is ValueError else type(error).__name__+': transfer failed. Details were saved for /logs.'
            await self.finish_error(task)
        finally:
            self.selection_events.pop(task.id,None)
            task.lanes.clear()
            try:
                await self.runtime.status.refresh_all()
            except Exception:
                self.runtime.logger.exception('Status cleanup failed task=%s',task.id)
            if task.phase in TERMINAL:
                self.tasks.pop(task.id,None)

    async def finish_error(self, task):
        try:
            await self.save(task)
            if not self.closing:
                await self.runtime.failed(task)
        except Exception:
            self.runtime.logger.exception('Could not save or send task failure task=%s',task.id)

    @property
    def max_file(self):
        return self.runtime.settings['max_file_gb']*1024**3

    async def transfer(self, task):
        if task.results and not task.collection and task.options['processing']=='none':
            return
        if task.source['kind']=='telegram' or (task.downloaded and not task.collection and (task.directory/'source').is_file()):
            task.file_count = 1
            return await self.process_file(task,task,None,task.name)
        task.stage('QueueDl')
        async with self.downloads.enter():
            task.stage('Resolve')
            plan = await resolve(task,task.source['url'],self.runtime.settings.get('collection_files',1000))
        task.collection = plan['collection']
        task.file_count = len(plan['items'])
        if not task.collection:
            item = plan['items'][0]
            if not task.options.get('name'):
                task.name = filename(item['name'].split('/')[-1])
            return await self.process_file(task,task,item,task.name)
        if task.options.get('select') or task.options.get('name'):
            raise ValueError('File rename and archive selection apply to individual files. Submit one file URL with -n or -e -s.')
        task.name = filename(plan.get('title') or 'Collection')
        task.stage('Pipeline')
        await self.save(task)
        async def process(pair):
            index,item = pair
            key = item['name']
            if task.options['processing']=='none' and self.confirmed(task,key):
                return
            directory = task.directory/'files'/str(index)
            directory.mkdir(parents=True,exist_ok=True)
            child = task.lane('file-'+str(index),key,directory)
            try:
                await self.process_file(task,child,item,key)
                shutil.rmtree(directory,ignore_errors=True)
            finally:
                task.lanes.pop('file-'+str(index),None)
        await self.parallel(enumerate(plan['items']),process)

    async def parallel(self, items, function):
        iterator = iter(items)
        async def consume():
            for item in iterator:
                await function(item)
        workers = [asyncio.create_task(consume()) for _ in range(self.runtime.settings.get('pipeline_files',3))]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                if not worker.done():worker.cancel()
            await asyncio.gather(*workers,return_exceptions=True)

    @staticmethod
    def confirmed(task, key):
        return any(result['source_name']==key for result in task.results)

    async def process_file(self, task, child, info, key):
        lease = await self.download(task,child,info)
        if child is task:
            key=child.name
        try:
            mode = task.options['processing']
            if mode == 'extract':
                await self.extract(task,child,key)
            elif mode in ('zip','7z'):
                await self.compress(task,child,mode,key)
            else:
                remote = key if task.collection else child.name
                # Video formats can change after a real audio/video merge.
                if task.collection and info and info['engine']=='yt_dlp':
                    remote = str(Path(key).with_suffix(Path(child.name).suffix))
                await self.upload(task,child,child.directory/'source',remote,key)
            (child.directory/'source').unlink(missing_ok=True)
            shutil.rmtree(child.directory/'archive',ignore_errors=True)
        finally:
            await self.resources.release(lease)

    async def download(self, task, child, info):
        source_file = child.directory/'source'
        marker = child.directory/'download.json'
        ready = None
        if marker.is_file():
            try:
                data = json.loads(marker.read_text())
                if source_file.is_file() and source_file.stat().st_size==data['size']:
                    ready=data
            except (OSError,ValueError,KeyError):pass
        if (child is task and task.downloaded and source_file.is_file()) or ready:
            if ready and not child.options.get('name'):child.name=ready['name']
            return await self.resources.storage(child,source_file,source_file.stat().st_size,self.max_file,known=True)
        child.stage('QueueDl')
        async with self.downloads.enter():
            if info:info=await refresh_item(child,info)
            total = int(info.get('total',0)) if info else 0
            if child.source['kind']=='telegram' and not info:
                message=await self.runtime.client.get_messages(child.source['chat'],ids=child.source['message'])
                total=int(message.file.size or 0) if message and message.file else 0
            factor=2 if info and info['engine']=='yt_dlp' else 1
            lease=await self.resources.storage(child,source_file,total*factor,self.max_file*factor)
            limit=min(self.max_file,lease.size//factor)
            settings={**self.runtime.settings,'byte_limit':limit}
            try:
                if info is None and child.source['kind']=='telegram':
                    await telegram_download.download(child,self.runtime.client,settings)
                elif info['engine']=='gdown':
                    child.stage('Download');child.engine='Google Drive'
                    await run_worker(child,'drive',[source_file,limit],secret={'url':info['url']})
                elif info['engine']=='yt_dlp':
                    child.stage('Download');child.engine='yt-dlp'
                    result=await run_worker(child,'media',[source_file,limit],secret={'url':info['url']})
                    if not child.options.get('name'):child.name=result['name']
                elif info['engine']=='mega':
                    child.stage('Download');child.engine='MEGA AES-CTR'
                    await run_worker(child,'mega',[source_file,limit],secret={'info':info})
                else:
                    await aria2_download.download(child,info,settings)
                if not source_file.is_file():
                    raise ValueError('The download engine did not create a complete local file.')
                if source_file.stat().st_size>limit:
                    raise ValueError('Downloaded file exceeds its storage budget.')
                self.runtime.logger.info('Download verified task=%s bytes=%s file=%s',task.id,source_file.stat().st_size,child.name)
                marker.write_text(json.dumps({'size':source_file.stat().st_size,'name':child.name}))
                if child is task:
                    task.downloaded=True
                    await self.save(task)
                return lease
            except BaseException:
                await self.resources.release(lease)
                raise

    async def upload(self, task, child, path, name, key):
        if self.confirmed(task,key):
            path.unlink(missing_ok=True)
            return
        prefix=task.options.get('path',task.destination['path'])
        remote='/'.join(p for p in (prefix,safe_path(name)) if p)
        lock_key=(*[task.destination[k] for k in ('namespace','repo_name','repo_type','branch')],remote)
        lock=self.paths.setdefault(lock_key,asyncio.Lock())
        receipt=path.with_name(path.name+'.uploaded.json')
        child.stage('QueueUp',path.stat().st_size)
        async with lock,self.uploads.enter():
            result=None
            if receipt.is_file():
                try:
                    data=json.loads(receipt.read_text())
                    if data['source_name']==key:result=data
                except (ValueError,KeyError):pass
            if result is None:
                profile=await self.db.user(task.user_id)
                if not profile['token']:raise ValueError('Set your HF token in /usettings, then retry.')
                child.engine='Hugging Face'
                result=await run_worker(child,'upload',[path,remote],secret={'token':profile['token'],'destination':task.destination})
                result['source_name']=key
                temporary=receipt.with_suffix('.pending')
                temporary.write_text(json.dumps(result));temporary.replace(receipt)
            result_lock=self.result_locks.setdefault(task.id,asyncio.Lock())
            async with result_lock:
                task.results.append(result)
                try:
                    await self.save(task)
                except BaseException:
                    task.results.pop()
                    raise
            # Delete only after both HF confirmation and durable upload bookkeeping.
            path.unlink(missing_ok=True);receipt.unlink(missing_ok=True)
            self.runtime.logger.info('HF upload confirmed and local file removed task=%s path=%s bytes=%s',task.id,result.get('path'),result.get('size'))
        child.stage('Complete')

    async def archive_path(self, child):
        folder=child.directory/'archive';folder.mkdir(exist_ok=True)
        archive=folder/filename(Path(child.name).name)
        if not archive.exists():archive.hardlink_to(child.directory/'source')
        return archive

    async def extract(self, task, child, key):
        archive=await self.archive_path(child)
        if not child.members:
            child.stage('Inspect')
            child.members=await run_worker(child,'list',[archive],secret={
                'password':task.options.get('password',''),'max_files':self.runtime.settings['archive_files'],
                'max_bytes':self.runtime.settings['archive_gb']*1024**3},timeout=120)
            child.selected=list(range(len(child.members)))
            if child is task:await self.save(task)
        if task.options.get('select'):
            task.stage('Select');await self.save(task)
            event=self.selection_events.setdefault(task.id,asyncio.Event())
            await self.runtime.offer_selection(task)
            await asyncio.wait_for(event.wait(),self.selection_limit)
            self.selection_events.pop(task.id,None)
            task.options['select']=False;await self.save(task)
        child.stage('Pipeline')
        if not hasattr(task,'member_slots'):
            task.member_slots=asyncio.Semaphore(self.runtime.settings.get('pipeline_files',3))
        prefix=(key.rsplit('.',1)[0]+'/') if task.collection else ''
        async def process(index):
            member=child.members[index];name=prefix+member['name']
            if self.confirmed(task,name):return
            directory=child.directory/'members'/str(index);directory.mkdir(parents=True,exist_ok=True)
            output=directory/'content'
            lane_key=str(child.directory)+':'+str(index)
            progress=task.lane(lane_key,member['name'],directory)
            lease=None
            try:
                lease=await self.resources.storage(progress,output,member['size'],self.max_file,known=True)
                async with self.extraction:
                    progress.stage('Extract',member['size']);progress.engine='Archive'
                    await run_worker(progress,'extract',[archive,member['original'],output,min(self.max_file,lease.size)],secret={'password':task.options.get('password','')})
                if output.stat().st_size!=member['size']:
                    raise ValueError('Extracted member size does not match the archive metadata.')
                await self.upload(task,progress,output,name,name)
                shutil.rmtree(directory,ignore_errors=True)
            finally:
                if lease:await self.resources.release(lease)
                task.lanes.pop(lane_key,None)
        async def bounded(index):
            async with task.member_slots:
                await process(index)
        await self.parallel(child.selected,bounded)

    async def compress(self, task, child, kind, key):
        source=await self.archive_path(child)
        output=child.directory/('packed.'+kind)
        estimate=min(self.max_file,(child.directory/'source').stat().st_size+1024**2)
        lease=await self.resources.storage(child,output,estimate,self.max_file)
        try:
            async with self.extraction:
                child.stage('Archive');child.engine='Python ZIP' if kind=='zip' else '7-Zip'
                await run_worker(child,'compress',[source,output,kind,estimate])
            await self.upload(task,child,output,key+'.'+kind,key+'.'+kind)
        finally:
            await self.resources.release(lease)

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
