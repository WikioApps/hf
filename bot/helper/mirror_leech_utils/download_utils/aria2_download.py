"""One aria2 process per task; real progress comes from its private local RPC."""
import asyncio
import secrets
import logging
import shutil
import socket
import time
import httpx
from bot.helper.ext_utils.process_utils import terminate
from bot.helper.ext_utils.files_utils import check_space
from bot.helper.ext_utils.resource_manager import allocated

async def download(task, info, settings):
    binary = shutil.which('aria2c')
    if not binary:
        raise ValueError('aria2c is missing. Run the build step to install requirements.')
    reserve = settings.get('disk_reserve_mb',1024)*1024**2
    check_space(task.directory,max(0,info.get('total',0)-allocated(task.directory/'source')),reserve=reserve)
    limit = settings.get('byte_limit',settings['max_file_gb'] * 1024**3)
    if info.get('total',0) > limit:
        raise ValueError('The file exceeds the configured size limit.')
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    sock.close()
    secret = secrets.token_hex(24)
    args = [binary, '--enable-rpc=true', '--rpc-listen-all=false', f'--rpc-listen-port={port}',
            f'--rpc-secret={secret}', '--max-connection-per-server=8', '--split=8',
            '--min-split-size=8M', '--continue=true', '--file-allocation=none',
            '--check-certificate=true', '--content-disposition=false', '--allow-overwrite=true', '--auto-file-renaming=false',
            '--max-tries=5', '--retry-wait=3', '--timeout=60', '--connect-timeout=20',
            '--console-log-level=error', '--summary-interval=0', '--max-concurrent-downloads=1']
    process = await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    async with httpx.AsyncClient(trust_env=False,timeout=10) as client:
        async def rpc(method, *params):
            response = await client.post(f'http://127.0.0.1:{port}/jsonrpc',json={
                'jsonrpc':'2.0','id':'task','method':'aria2.'+method,'params':['token:'+secret,*params]})
            data = response.json()
            if 'error' in data:
                raise ValueError('aria2 rejected the download request.')
            return data['result']
        try:
            for attempt in range(50):
                if process.returncode is not None:
                    raise ValueError('aria2 could not start its local RPC service.')
                try:
                    await rpc('getVersion')
                    break
                except httpx.TransportError:
                    await asyncio.sleep(.1)
            options = {'dir':str(task.directory),'out':'source','header':[
                f'{k}: {v}' for k,v in info.get('headers',{}).items() if '\n' not in str(v) and '\r' not in str(v) and '\n' not in str(k) and '\r' not in str(k)]}
            gid = await rpc('addUri',[info['url']],options)
            task.engine = 'aria2 1.37'
            task.stage('Download',info.get('total',0))
            while True:
                data = await rpc('tellStatus',gid)
                task.done = int(data.get('completedLength',0))
                task.total = int(data.get('totalLength',0))
                if max(task.done,task.total) > limit:
                    raise ValueError('Download exceeds the configured file size.')
                check_space(task.directory,reserve=reserve)
                manager = getattr(task,'resource_manager',None)
                lease = getattr(task,'storage_lease',None)
                if manager and lease: manager.check(lease)
                if data['status'] == 'complete':
                    target = task.directory/'source'
                    if not target.is_file():
                        logging.getLogger(__name__).error('aria2 missing output task=%s files=%s',task.id,data.get('files',[]))
                        raise ValueError('aria2 completed without the expected local file. Retry after updating the bot.')
                    actual = target.stat().st_size
                    if (task.total and actual != task.total) or (info.get('total') and actual != int(info['total'])):
                        raise ValueError('Downloaded file size does not match the source. Retry the download.')
                    return target
                if data['status'] in ('error','removed'):
                    raise ValueError('aria2 download failed (code '+str(data.get('errorCode','unknown'))+'). Check the link or retry.')
                await asyncio.sleep(.25)
        finally:
            await terminate(process)
