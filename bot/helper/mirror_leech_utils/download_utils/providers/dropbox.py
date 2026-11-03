from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
DOMAINS = ('dropbox.com','dropboxusercontent.com')


async def resolve(ctx,url):
    parts=urlsplit(url)
    # rlkey is required for current shared links and must be preserved.
    query=[(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k not in ('dl','raw')]
    direct=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query+[('dl','1')]),''))
    from .direct import resolve
    await resolve(ctx,direct)
