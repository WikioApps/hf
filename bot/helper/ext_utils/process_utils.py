import asyncio
import json
import os
import sys
import signal
from bot.core.config_manager import ROOT
from bot.helper.ext_utils.files_utils import check_space


async def terminate(process):
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def run_worker(task, mode, arguments, *, secret=None, timeout=None):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'bot.helper.ext_utils.worker', mode, *map(str,arguments),
        cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=32*1024**2, start_new_session=True)
    async def collect():
        result, error = None, None
        process.stdin.write(json.dumps(secret or {}).encode()+b'\n')
        await process.stdin.drain()
        process.stdin.close()
        while line := await process.stdout.readline():
            event = json.loads(line)
            if 'phase' in event and task.phase != event['phase']:
                task.stage(event['phase'], event.get('total',task.total))
            if 'done' in event:
                task.done = event['done']
            if 'total' in event:
                task.total = event['total']
            if 'name' in event and not task.options.get('name'):
                task.name = event['name']
            if 'error' in event:
                error = event['error']
            if 'result' in event:
                result = event['result']
        code = await process.wait()
        if code:
            raise ValueError(error or f'{mode} exited with code {code}.')
        return result
    try:
        async with asyncio.timeout(timeout):
            return await collect()
    finally:
        await terminate(process)
