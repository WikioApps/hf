from urllib.parse import urlsplit,urlunsplit
DOMAINS=('gitlab.com',)


async def resolve(ctx,url):
    p=urlsplit(url)
    url=urlunsplit((p.scheme,p.netloc,p.path.replace('/-/blob/','/-/raw/',1),p.query,''))
    from .direct import resolve
    await resolve(ctx,url)
