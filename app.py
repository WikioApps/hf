"""Run the Telegram bot and HTTP portal in one supervised process."""
import asyncio
import logging
import os
from pathlib import Path
import sys
import uvicorn
from bot.core.config_manager import Config, ROOT
from bot.core.runtime import Runtime
from web.backend.application import create_app


async def serve(config):
    runtime = Runtime(config)
    server = uvicorn.Server(uvicorn.Config(create_app(runtime),host='0.0.0.0',port=config.port,
                                          log_level='info',access_log=False,timeout_graceful_shutdown=20))
    async def monitor():
        await runtime.shutdown_requested.wait()
        server.should_exit = True
    watcher = asyncio.create_task(monitor())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher,return_exceptions=True)
    if not server.started or runtime.shutdown_requested.is_set():
        raise SystemExit(1)


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    for name in ('httpx','httpcore','telethon'):
        logging.getLogger(name).setLevel(logging.WARNING)
    os.environ['PATH'] = str(Path(sys.executable).parent)+os.pathsep+str(ROOT/'tools')+os.pathsep+os.environ.get('PATH','')
    try:
        config = Config.load()
    except ValueError as error:
        logging.error('%s',error)
        raise SystemExit(2) from None
    asyncio.run(serve(config))


if __name__ == '__main__':
    main()
