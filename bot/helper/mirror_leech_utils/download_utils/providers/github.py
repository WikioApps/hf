from urllib.parse import urlsplit,urlunsplit
DOMAINS = ('github.com','raw.githubusercontent.com','gist.githubusercontent.com')


async def resolve(ctx,url):
    p=urlsplit(url)
    if p.hostname=='github.com' and '/blob/' in p.path:
        url=urlunsplit(('https','raw.githubusercontent.com',p.path.replace('/blob/','/',1),p.query,''))
    from .direct import resolve
    await resolve(ctx,url)
