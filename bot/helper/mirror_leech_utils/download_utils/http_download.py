"""Download small public files without starting a separate aria2 process."""
from urllib.parse import urljoin, urlparse
import httpx
from bot.helper.ext_utils.network_utils import public_url
from bot.helper.ext_utils.files_utils import check_space, filename
from .resolver import content_name, USER_AGENT

SMALL_FILE = 16*1024**2


async def download(task, info, settings):
    if int(info.get('total') or 0) > SMALL_FILE:
        return False
    url = info['url']
    headers = {'User-Agent':USER_AGENT,'Accept-Encoding':'identity',**info.get('headers',{})}
    limit = settings['byte_limit']
    reserve = settings.get('disk_reserve_mb',1024)*1024**2
    async with httpx.AsyncClient(timeout=httpx.Timeout(30,connect=15),follow_redirects=False) as client:
        for _ in range(10):
            await public_url(url)
            async with client.stream('GET',url,headers=headers) as response:
                if response.is_redirect:
                    target = urljoin(str(response.url),response.headers['location'])
                    if urlparse(target).netloc != urlparse(url).netloc:
                        headers = {k:v for k,v in headers.items() if k.lower() not in ('cookie','authorization')}
                    url = target
                    continue
                response.raise_for_status()
                total = int(response.headers.get('content-length') or 0)
                if response.headers.get('content-encoding','identity') != 'identity':
                    return False
                if not total or total > SMALL_FILE:
                    return False
                if total > limit:
                    raise ValueError('The file exceeds the configured size limit.')
                kind = response.headers.get('content-type','').lower()
                if 'text/html' in kind and not task.name.lower().endswith(('.html','.htm')):
                    raise ValueError('The host returned a webpage instead of a file. Use a public download link.')
                if not task.options.get('name') and task.name == 'download.bin':
                    task.name = filename(content_name(response.headers,str(response.url)))
                task.engine = 'HTTP'
                task.stage('Download',total)
                path = task.directory/'source'
                with path.open('wb') as output:
                    async for chunk in response.aiter_bytes(256*1024):
                        if task.done+len(chunk) > limit:
                            raise ValueError('The file exceeds the configured size limit.')
                        check_space(task.directory,len(chunk),reserve=reserve)
                        manager = getattr(task,'resource_manager',None)
                        lease = getattr(task,'storage_lease',None)
                        if manager and lease: manager.check(lease)
                        output.write(chunk)
                        task.done += len(chunk)
                if task.done != total or (info.get('total') and task.done != int(info['total'])):
                    raise ValueError('Incomplete HTTP download. Retry the source link.')
                return True
        raise ValueError('The host redirected too many times.')
