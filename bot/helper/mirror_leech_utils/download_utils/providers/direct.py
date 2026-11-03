from ..resolver import content_name


async def resolve(ctx,url):
    result=await ctx.request(url,probe=True)
    kind=result['headers'].get('content-type','').lower()
    if any(x in kind for x in ('text/html','application/xhtml','mpegurl','dash+xml')) or url.split('?')[0].endswith(('.m3u8','.mpd')):
        await ctx.media(result['url'])
        return
    ctx.add(result['url'],content_name(result['headers'],result['url']),result['headers'].get('content-length',0))
