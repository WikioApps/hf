"""MediaFire public file pages and paginated folder listings."""
import re
from urllib.parse import urlparse, parse_qs, urljoin, unquote
from bot.helper.ext_utils.files_utils import filename
from bs4 import BeautifulSoup
from ..resolver import content_name

DOMAINS = ('mediafire.com',)
API = 'https://www.mediafire.com/api/1.4/'


async def call(ctx,method,params):
    from urllib.parse import urlencode
    data = await ctx.json(API+method+'.php?'+urlencode({**params,'response_format':'json'}))
    value = data.get('response',{})
    if value.get('result') != 'Success':
        raise ValueError('MediaFire could not open this public file or folder.')
    return value


async def resolve_file(ctx,url,name='',size=0):
    response = await ctx.request(url,probe=True)
    if 'text/html' not in response['headers'].get('content-type','text/html'):
        ctx.add(response['url'],name or content_name(response['headers'],response['url']),size or response['headers'].get('content-length',0))
        return
    soup = BeautifulSoup(response['body'],'html.parser')
    link = soup.select_one('a#downloadButton, a.input.popsok')
    if not link or not link.get('href','').startswith(('https://','http://')):
        match=re.search(r"afI\s*=\s*[\"']([a-z0-9]+)",response['body'].decode(errors='replace'))
        if match:return await resolve(ctx,'https://www.mediafire.com/folder/'+match[1])
        raise ValueError('MediaFire did not provide a public download button. The file may be removed or restricted.')
    ctx.add(link['href'],name or filename(unquote(link.get('download') or urlparse(link['href']).path.rsplit('/',1)[-1])),size,
            headers={'Referer':url})



async def resolve(ctx,url):
    parts = urlparse(url).path.strip('/').split('/')
    if parts[0] != 'folder':
        return await resolve_file(ctx,url)
    root = parts[1]
    pending,seen = [(root,'')],set()
    while pending:
        key,prefix = pending.pop(0)
        if key in seen:continue
        seen.add(key)
        if len(seen)>ctx.limit:raise ValueError('MediaFire folder tree exceeds the collection limit.')
        for kind in ('files','folders'):
            chunk = 1
            while True:
                data = await call(ctx,'folder/get_content',{'folder_key':key,'content_type':kind,'chunk':chunk,'chunk_size':1000})
                content = data.get('folder_content',{})
                for item in content.get(kind,[]):
                    if kind == 'folders':
                        pending.append((item['folderkey'],prefix+filename(item['name'])+'/'))
                    else:
                        name = prefix+filename(item['filename'])
                        page = item.get('links',{}).get('normal_download') or 'https://www.mediafire.com/file/'+item['quickkey']
                        # Resolve expiring download links immediately before the transfer.
                        ctx.add(page,name,item.get('size',0),engine='deferred',provider='mediafire')
                if content.get('more_chunks') != 'yes':break
                chunk += 1
    ctx.title = 'MediaFire folder'
