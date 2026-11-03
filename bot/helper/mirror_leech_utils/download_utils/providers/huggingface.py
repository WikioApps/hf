from urllib.parse import urlparse, quote
DOMAINS = ('huggingface.co',)


async def resolve(ctx,url):
    parts=urlparse(url).path.split('/')
    if 'blob' in parts:
        parts[parts.index('blob')]='resolve'
        url='https://huggingface.co'+'/'.join(parts)
    from .direct import resolve
    await resolve(ctx,url)
