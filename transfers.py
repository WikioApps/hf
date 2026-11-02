"""HTTP/Telegram ingress, link resolution and isolated HF/archive workers."""
import asyncio
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time
from urllib.parse import urlparse, unquote, parse_qs
import httpx
from config import ROOT, filename


async def public_url(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Only public HTTP/HTTPS URLs without embedded credentials are accepted')
    try:
        address = ipaddress.ip_address(p.hostname)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global: raise ValueError('Local, private and reserved network destinations are blocked')
        return url
    try:
        infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM), 20)
    except OSError:
        raise ValueError('Cannot resolve download host') from None
    if not infos or any(not ipaddress.ip_address(i[4][0]).is_global for i in infos):
        raise ValueError('Local, private and reserved network destinations are blocked')
    return url


def check_space(cfg, required=0):
    if shutil.disk_usage(cfg.data).free < cfg.reserve + max(required, 0):
        raise ValueError('Insufficient disk space; free space or lower concurrency/file size')


async def worker(job, mode, *args, cfg, observe=None):
    env = os.environ.copy()
    env.update(HF_TOKEN=cfg.hf_token, REPO_ID=cfg.repo_id, REPO_TYPE=cfg.repo_type, BRANCH=cfg.branch,
               HF_HUB_DISABLE_XET='1', HF_HUB_ENABLE_HF_TRANSFER='0')
    proc = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'worker.py'), mode,
            *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=env, limit=16 * 1024 * 1024)
    job.process = proc
    result, error = None, None
    async def monitor():
        while proc.returncode is None:
            await asyncio.sleep(.5)
            check_space(cfg)
            candidates = ([observe] if observe and observe.exists() else list(observe.parent.glob(observe.name + '*.part')) if observe else [])
            if candidates:
                size = max(p.stat().st_size for p in candidates)
                if size > cfg.max_bytes: raise ValueError('File exceeds MAX_FILE_GB')
                job.advance(size)
    watch = asyncio.create_task(monitor())
    async def read():
        nonlocal result, error
        while line := await proc.stdout.readline():
            data = json.loads(line)
            if 'error' in data: error = data['error']
            if 'name' in data and not job.rename: job.name = filename(data['name'])
            if 'result' in data: result = data['result']
            if 'phase' in data and job.phase != data['phase']:
                job.stage(data['phase'], data.get('total', job.total))
            if 'done' in data: job.advance(data['done'])
        return await proc.wait()
    reader = asyncio.create_task(read())
    try:
        done, _ = await asyncio.wait([reader, watch], return_when=asyncio.FIRST_COMPLETED)
        if watch in done:
            watch.result()
        code = await reader
        if code: raise ValueError(error or f'{mode} worker exited with code {code}')
        return result
    finally:
        watch.cancel(); reader.cancel()
        if proc.returncode is None:
            proc.terminate()
            try: await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
        await asyncio.gather(watch, reader, return_exceptions=True)
        job.process = None


async def download_http(job, url, target, cfg, headers=None, resolve=True):
    headers = {'User-Agent': 'Mozilla/5.0 HF-Mirror/1.0', 'Accept-Encoding': 'identity', **(headers or {})}
    # Never forward site-provided credentials to a new origin during redirects.
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), follow_redirects=False) as client:
        for _ in range(10):
            await public_url(url)
            async with client.stream('GET', url, headers=headers) as response:
                if response.is_redirect:
                    dest = str(response.url.join(response.headers['location']))
                    if urlparse(dest).netloc != urlparse(url).netloc:
                        headers = {k:v for k,v in headers.items() if k.lower() not in ('authorization','cookie','referer')}
                    url = dest; continue
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').lower()
                if 'text/html' in content_type and resolve:
                    # Landing pages must never be silently uploaded as the requested file.
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024**2: break
                    if 'mediafire.com' == urlparse(url).hostname or urlparse(url).hostname.endswith('.mediafire.com'):
                        from bs4 import BeautifulSoup
                        link = BeautifulSoup(body, 'html.parser').select_one('a#downloadButton')
                        if link and link.get('href'):
                            return await download_http(job, link['href'], target, cfg, resolve=False)
                    info = await worker(job, 'resolve', url, cfg=cfg)
                    job.name = filename(info['name'])
                    return await download_http(job, info['url'], target, cfg, headers=info['headers'], resolve=False)
                if 'text/html' in content_type:
                    raise ValueError('Link returned an HTML page instead of a downloadable file')
                total = int(response.headers.get('content-length', 0))
                if total > cfg.max_bytes: raise ValueError('File exceeds MAX_FILE_GB')
                check_space(cfg, total)
                if not job.rename:
                    cd = response.headers.get('content-disposition', '')
                    match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", cd, re.I)
                    name = unquote((match.group(1) or match.group(2)).strip()) if match else unquote(Path(urlparse(url).path).name)
                    if name: job.name = filename(name)
                job.stage('Download', total)
                with target.open('wb') as f:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        if job.done + len(chunk) > cfg.max_bytes: raise ValueError('File exceeds MAX_FILE_GB')
                        check_space(cfg, len(chunk)); f.write(chunk); job.advance(job.done + len(chunk))
                if total and job.done != total: raise ValueError('Incomplete download')
                return target
        raise ValueError('Too many redirects')


async def download(job, cfg, client, user_client):
    target = job.directory / 'source'
    job.stage('Download')
    source = job.source
    if isinstance(source, str) and source.startswith(('https://t.me/', 'http://t.me/')):
        p = urlparse(source).path.strip('/').split('/')
        try:
            mid = int(p[-1]); entity = int('-100' + p[1]) if p[0] == 'c' else p[0]
            # A user session is privileged: only the owner may request links through it.
            tc = user_client if user_client and job.user_id == cfg.owner_id else client
            source = await tc.get_messages(entity, ids=mid)
            if not source or not source.file: raise ValueError('Message has no downloadable file')
        except (ValueError, IndexError): raise ValueError('Invalid or inaccessible Telegram message link') from None
    if not isinstance(source, str):
        job.engine = 'Telegram MTProto'
        if source.file.size > cfg.max_bytes: raise ValueError('File exceeds MAX_FILE_GB')
        check_space(cfg, source.file.size)
        if not job.rename: job.name = filename(source.file.name or f'telegram_{source.id}{source.file.ext or ".bin"}')
        job.stage('Download', source.file.size)
        def progress(current, total):
            check_space(cfg); job.total = total; job.advance(current)
        tc = locals().get('tc', client)
        await tc.download_media(source, file=str(target), progress_callback=progress)
        if not target.exists() or target.stat().st_size != source.file.size:
            raise ValueError('Incomplete Telegram download')
        return target
    await public_url(source)
    p = urlparse(source)
    if p.hostname in ('drive.google.com', 'docs.google.com'):
        job.engine = 'Google Drive / gdown'
        await worker(job, 'drive', source, target, cfg=cfg, observe=target)
        if not target.exists() or target.stat().st_size > cfg.max_bytes: raise ValueError('Google Drive file missing or exceeds MAX_FILE_GB')
        job.advance(target.stat().st_size)
        return target
    if p.hostname == 'pixeldrain.com' and p.path.startswith('/u/'):
        source = 'https://pixeldrain.com/api/file/' + p.path.split('/')[2] + '?download'
    if p.hostname in ('www.dropbox.com', 'dropbox.com'):
        source = source.split('?')[0] + '?dl=1'
    job.engine = 'HTTP / Python'
    return await download_http(job, source, target, cfg)
