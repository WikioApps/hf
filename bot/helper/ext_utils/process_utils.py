import asyncio
import json
import logging
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
    manager=getattr(task,'resource_manager',None)
    if manager:
        async with manager.worker(task) as budget:
            return await _run_worker(task,mode,arguments,secret=secret,timeout=timeout,memory_limit=budget)
    return await _run_worker(task,mode,arguments,secret=secret,timeout=timeout)


async def _run_worker(task, mode, arguments, *, secret=None, timeout=None, memory_limit=None):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'bot.helper.ext_utils.worker', mode, *map(str,arguments),
        cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=32*1024**2, start_new_session=True)
    async def capture_errors():
        logger = logging.getLogger('hfbot.worker')
        while line := await process.stderr.readline():
            text = line.decode('utf-8',errors='replace').rstrip()[:8192]
            if text:
                logger.info('task=%s operation=%s %s',task.id,mode,text)
    stderr_task = asyncio.create_task(capture_errors())
    from bot.helper.ext_utils.resource_manager import memory_available,MIB
    import psutil
    cap=int(memory_limit or max(192*MIB,min(512*MIB,int(memory_available()*.65))))
    resource_error=[]
    async def monitor_memory():
        while process.returncode is None:
            await asyncio.sleep(.25)
            manager = getattr(task,'resource_manager',None)
            lease = getattr(task,'storage_lease',None)
            if manager and lease:
                try:
                    manager.check(lease)
                except ValueError as error:
                    resource_error.append(str(error))
                    await terminate(process)
                    return
            try:
                parent=psutil.Process(process.pid)
                usage=sum(p.memory_info().rss for p in [parent,*parent.children(recursive=True)] if p.is_running())
                if usage>cap or (memory_available()<48*MIB and usage>96*MIB):
                    resource_error.append('This worker exceeded its memory budget. Other tasks were left running. Lower concurrency or use a host with more RAM.')
                    await terminate(process)
                    return
            except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    memory_task=asyncio.create_task(monitor_memory())
    async def collect():
        result, error = None, None
        manager = getattr(task,'resource_manager',None)
        process.stdin.write(json.dumps({**(secret or {}),'_memory_limit_bytes':cap,
            '_disk_reserve_bytes':manager.reserve if manager else 512*1024**2}).encode()+b'\n')
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
        if resource_error:
            raise ValueError(resource_error[0])
        if code:
            raise ValueError(error or f'{mode} exited with code {code}.')
        return result
    try:
        async with asyncio.timeout(timeout):
            return await collect()
    finally:
        memory_task.cancel()
        await terminate(process)
        await asyncio.gather(stderr_task,memory_task,return_exceptions=True)
