"""Resolve landing pages before choosing the transfer engine."""
from pathlib import Path
from urllib.parse import urlparse, unquote
import re
import httpx
from bs4 import BeautifulSoup
from bot.helper.ext_utils.network_utils import public_url
from bot.helper.ext_utils.files_utils import filename
from bot.helper.ext_utils.process_utils import run_worker

async def resolve(task, url):
    await public_url(url)
    host = urlparse(url).hostname
    if host in ('drive.google.com','docs.google.com'):
        return {'engine':'gdown','url':url}
    if host == 'pixeldrain.com' and urlparse(url).path.startswith('/u/'):
        url = 'https://pixeldrain.com/api/file/'+urlparse(url).path.split('/')[2]+'?download'
    if host in ('dropbox.com','www.dropbox.com'):
        url = url.split('?')[0]+'?dl=1'
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for _ in range(10):
            await public_url(url)
            async with client.stream('GET',url,headers={'Accept-Encoding':'identity','User-Agent':'Mozilla/5.0'}) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers['location']))
                    continue
                response.raise_for_status()
                if 'text/html' not in response.headers.get('content-type',''):
                    cd = response.headers.get('content-disposition','')
                    match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)",cd,re.I)
                    name = unquote((match.group(1) or match.group(2)).strip()) if match else unquote(Path(urlparse(url).path).name)
                    return {'engine':'aria2','url':url,'name':filename(name or 'download.bin'),
                            'total':int(response.headers.get('content-length',0))}
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2*1024**2:
                        break
                if host == 'mediafire.com' or host.endswith('.mediafire.com'):
                    link = BeautifulSoup(body,'html.parser').select_one('a#downloadButton')
                    if link and link.get('href'):
                        url = link['href']
                        continue
                info = await run_worker(task,'resolve',[url],timeout=90)
                await public_url(info['url'])
                return {'engine':'aria2',**info}
    raise ValueError('Too many redirects.')
