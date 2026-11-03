"""Bounded public-link resolution shared by site adapters."""
import json
from pathlib import PurePosixPath
import re
from urllib.parse import unquote, urlparse, urljoin
import httpx
from bs4 import BeautifulSoup
from bot.helper.ext_utils.network_utils import public_url
from bot.helper.ext_utils.files_utils import filename, safe_path
from bot.helper.ext_utils.process_utils import run_worker

USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'


def host_matches(host, domains):
    return any(host == name or host.endswith('.'+name) for name in domains)


def content_name(headers, url):
    cd = headers.get('content-disposition','')
    match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)",cd,re.I)
    name = unquote((match.group(1) or match.group(2)).strip()) if match else unquote(PurePosixPath(urlparse(url).path).name)
    return filename(name or 'download.bin')


class Context:
    def __init__(self, task, limit=1000):
        self.task, self.limit = task, limit
        self.items, self.seen = [], set()
        self.title = ''
        self.requests = 0
        self.client = httpx.AsyncClient(timeout=30,follow_redirects=False,
            headers={'User-Agent':USER_AGENT,'Accept-Encoding':'identity'})

    async def __aenter__(self):
        return self

    async def __aexit__(self,*args):
        await self.client.aclose()

    async def request(self, url, method='GET', *, headers=None, data=None, json_data=None, probe=False):
        headers = dict(headers or {})
        for _ in range(10):
            self.requests += 1
            if self.requests > max(200,self.limit*4):
                raise ValueError('The site required too many requests. Use a smaller folder or album.')
            await public_url(url)
            async with self.client.stream(method,url,headers=headers,data=data,json=json_data) as response:
                if response.is_redirect:
                    target = urljoin(str(response.url),response.headers['location'])
                    if urlparse(target).netloc != urlparse(url).netloc:
                        headers = {k:v for k,v in headers.items() if k.lower() not in ('cookie','authorization')}
                    url,method,data,json_data = target,'GET',None,None
                    continue
                if response.status_code in (401,403):
                    raise ValueError('The host requires login, permission, or a browser challenge. Try a public direct link.')
                if response.status_code == 429:
                    raise ValueError('The host rate-limited this server. Retry later.')
                response.raise_for_status()
                kind = response.headers.get('content-type','').lower()
                result = {'url':str(response.url),'headers':dict(response.headers),'body':b''}
                if probe and 'text/html' not in kind and 'application/xhtml' not in kind:
                    return result
                chunks, length = [],0
                async for chunk in response.aiter_bytes(65536):
                    length += len(chunk)
                    if length > 8*1024**2:
                        raise ValueError('The host response exceeds the metadata limit.')
                    chunks.append(chunk)
                result['body'] = b''.join(chunks)
                return result
        raise ValueError('The host redirected too many times.')

    async def json(self,url,**kwargs):
        result = await self.request(url,**kwargs)
        try:
            return json.loads(result['body'])
        except (ValueError,UnicodeDecodeError):
            raise ValueError('The host returned an unexpected response. It may require a browser challenge.') from None

    async def soup(self,url,**kwargs):
        result = await self.request(url,**kwargs)
        return BeautifulSoup(result['body'],'html.parser')

    def add(self,url,name='',total=0,engine='aria2',headers=None,**extra):
        name = safe_path(name) if name else content_name({},url)
        key = (url,name)
        if key in self.seen:
            return
        if len(self.items) >= self.limit:
            raise ValueError('The collection exceeds the configured file limit. Use a smaller album or folder.')
        self.seen.add(key)
        self.items.append({'url':url,'name':name,'total':max(0,int(total or 0)),
                           'engine':engine,'headers':headers or {},**extra})

    async def media(self,url,engine='media'):
        result = await run_worker(self.task,'catalog',[],secret={'url':url,'engine':engine,'limit':self.limit},timeout=180)
        if not result:
            raise ValueError('No public downloadable media was found. This link may require login or cookies.')
        for item in result['items']:
            self.add(**item)
        self.title = result.get('title','')

    def result(self):
        if not self.items:
            raise ValueError('The link contains no publicly downloadable files.')
        # Stable names also make retries distinguish repeated names in a collection.
        used = set()
        for item in self.items:
            original = PurePosixPath(item['name']); name = str(original); count = 1
            while name in used:
                count += 1
                name = str(original.with_name(f'{original.stem} ({count}){original.suffix}'))
            item['name'] = name
            used.add(name)
        return {'items':self.items,'title':self.title,'collection':len(self.items)>1}
