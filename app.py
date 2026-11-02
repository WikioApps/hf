"""Single-process ASGI health service and supervised Telegram runtime."""
import asyncio
import fcntl
import json
import logging
import os
import sys
from pathlib import Path
from config import Config


class JsonLog(logging.Formatter):
    def format(self, record):
        # Avoid dumping exceptions/URLs/environment. Events and error types identify failures.
        data = {'level':record.levelname, 'logger':record.name, 'event':record.getMessage()}
        for key in ('task','error_type'):
            if hasattr(record,key): data[key] = getattr(record,key)
        if record.exc_info: data['exception'] = record.exc_info[0].__name__
        return json.dumps(data)


class Application:
    def __init__(self, cfg, bot_factory=None):
        self.cfg = cfg; self.bot = None; self.lock = None; self.server = None
        self.bot_factory = bot_factory
    async def startup(self):
        from bot import Bot
        from huggingface_hub import HfApi
        self.cfg.data.mkdir(parents=True,exist_ok=True)
        os.chmod(self.cfg.data,0o700)
        self.lock = (self.cfg.data/'instance.lock').open('a')
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another bot instance is running in DATA_DIR') from None
        def check_hf():
            api = HfApi(token=self.cfg.hf_token)
            api.whoami()
            api.create_repo(self.cfg.repo_id,repo_type=self.cfg.repo_type,private=self.cfg.private_repo,exist_ok=True)
            if self.cfg.branch != 'main':
                api.create_branch(self.cfg.repo_id,branch=self.cfg.branch,repo_type=self.cfg.repo_type,exist_ok=True)
        await asyncio.to_thread(check_hf)
        self.bot = (self.bot_factory or Bot)(self.cfg)
        await self.bot.start()
        def failed(task):
            if not task.cancelled() and task.exception():
                logging.getLogger('hf.app').error('bot_supervisor_failed')
                if self.server: self.server.should_exit = True
        self.bot.loop_task.add_done_callback(failed)
    async def shutdown(self):
        if self.bot: await self.bot.close()
        if self.lock: self.lock.close(); self.lock = None
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    try:
                        await self.startup(); await send({'type':'lifespan.startup.complete'})
                    except Exception as exc:
                        await self.shutdown()
                        await send({'type':'lifespan.startup.failed','message':f'Startup failed: {type(exc).__name__}. Check Telegram/HF credentials, repository permissions, DATA_DIR and outbound network.'})
                        return
                elif event['type'] == 'lifespan.shutdown':
                    await self.shutdown(); await send({'type':'lifespan.shutdown.complete'}); return
        elif scope['type'] == 'http':
            healthy = bool(self.bot and self.bot.healthy())
            code = (200 if healthy else 503) if scope['path'] in ('/','/healthz') else 404
            body = json.dumps({'service':'hf-mirror','status':'ok' if code == 200 else 'unavailable' if code == 503 else 'not_found'}).encode()
            await send({'type':'http.response.start','status':code,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'no-store')]})
            await send({'type':'http.response.body','body':body})


def main():
    os.umask(0o077)
    os.environ['PATH'] = str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    handler = logging.StreamHandler(); handler.setFormatter(JsonLog())
    logging.basicConfig(level=logging.INFO,handlers=[handler])
    for name in ('telethon','httpx','httpcore','huggingface_hub'): logging.getLogger(name).setLevel(logging.ERROR)
    try: cfg = Config.load()
    except ValueError as e:
        logging.getLogger('hf.app').error(str(e)); return 2
    if '--check-config' in sys.argv:
        print('Configuration valid; no network authentication performed.'); return 0
    import uvicorn
    app = Application(cfg)
    server = uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=cfg.port,workers=1,access_log=False,log_level='info'))
    app.server = server
    server.run()
    return 0 if server.started and not (app.bot and app.bot.loop_task and not app.bot.loop_task.cancelled() and app.bot.loop_task.exception()) else 1


if __name__ == '__main__': sys.exit(main())
