"""Cyberdrop's public no-JavaScript album pages and file API."""
from urllib.parse import urlparse,urljoin
from bot.helper.ext_utils.files_utils import filename
DOMAINS=('cyberdrop.cr','cyberdrop.me','cyberdrop.to','cyberdrop.cc')


async def file(ctx,ident):
    info=await ctx.json('https://api.cyberdrop.cr/api/file/info/'+ident)
    auth=await ctx.json('https://api.cyberdrop.cr/api/file/auth/'+ident)
    if not auth.get('url'):raise ValueError('Cyberdrop did not provide a public download URL.')
    ctx.add(auth['url'],filename(info['name']),info.get('size',0))


async def resolve(ctx,url):
    parts=urlparse(url).path.strip('/').split('/')
    if len(parts)==2 and parts[0]=='a':
        soup=await ctx.soup(url.split('?')[0]+'?nojs')
        title=soup.select_one('#title');ctx.title=title.get_text(strip=True) if title else 'Cyberdrop album'
        for link in soup.select('a#file[href]'):
            target=urljoin(url,link['href'])
            ctx.add(target,filename(link.get('title') or target.rsplit('/',1)[-1]),engine='deferred',provider='cyberdrop')
    elif len(parts)==2 and parts[0] in ('f','e'):
        await file(ctx,parts[1])
    else:
        from .direct import resolve
        await resolve(ctx,url)
