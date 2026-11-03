"""Delete only this bot's inactive task files and scoped MongoDB history."""
import asyncio
from datetime import datetime, timedelta, timezone
import re
import shutil
import time

from bot.helper.mirror_leech_utils.status_utils.task_status import TERMINAL


class Maintenance:
    def __init__(self, manager):
        self.manager = manager
        self.runtime = manager.runtime
        self.db = manager.db

    async def remove_local(self, tid):
        if not re.fullmatch(r'[a-f0-9]{12}',tid):
            raise ValueError('Invalid task ID.')
        path = self.runtime.config.data/'tasks'/tid
        def remove():
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        try:
            await asyncio.to_thread(remove)
        except OSError:
            self.runtime.logger.exception('Local task cleanup failed task=%s',tid)
        async with self.manager.resources.condition:
            self.manager.resources.condition.notify_all()

    async def clear_history(self, uid):
        count = 0
        async with self.manager.submissions:
            query = {'bot':self.db.scope,'phase':{'$in':list(TERMINAL)},
                     '_id':{'$nin':list(self.manager.tasks)}}
            if uid != self.runtime.config.owner_id:
                query['user_id'] = uid
            while records := await self.db.db.tasks.find(query,{'_id':1}).limit(200).to_list():
                ids = [record['_id'] for record in records]
                for tid in ids:
                    await self.remove_local(tid)
                count += await self.db.delete_records(ids)
        return count

    async def sweep(self):
        # The submission lock prevents retries racing with deletion of their files.
        async with self.manager.submissions:
            root = self.runtime.config.data/'tasks'
            entries = await asyncio.to_thread(lambda: list(root.iterdir()) if root.exists() else [])
            retention = self.runtime.settings.get('failed_file_minutes',15)*60
            for path in entries:
                if path.name in self.manager.tasks or not re.fullmatch(r'[a-f0-9]{12}',path.name):
                    continue
                record = await self.db.db.tasks.find_one({'bot':self.db.scope,'_id':path.name},
                                                        {'phase':1,'updated':1})
                if record and record['phase'] not in TERMINAL:
                    await self.db.db.tasks.update_one({'bot':self.db.scope,'_id':path.name},
                        {'$set':{'phase':'Interrupted','error':'Worker stopped. Retry the source link.'}})
                    record['phase'] = 'Interrupted'
                stamp = record.get('updated') if record else None
                try:
                    touched = stamp.replace(tzinfo=timezone.utc).timestamp() if stamp else path.stat().st_mtime
                except FileNotFoundError:
                    continue
                immediate = record and record['phase'] in ('Complete','Cancelled')
                if immediate or time.time()-touched >= retention:
                    await self.remove_local(path.name)
            cutoff = datetime.now(timezone.utc)-timedelta(days=self.runtime.settings.get('history_days',7))
            query = {'bot':self.db.scope,'phase':{'$in':list(TERMINAL)},
                     '_id':{'$nin':list(self.manager.tasks)},'updated':{'$lt':cutoff}}
            while records := await self.db.db.tasks.find(query,{'_id':1}).limit(200).to_list():
                ids = [record['_id'] for record in records]
                for tid in ids:
                    await self.remove_local(tid)
                await self.db.delete_records(ids)

    async def run(self):
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.runtime.logger.exception('Scheduled task cleanup failed; retrying next minute')
            await asyncio.sleep(60)
