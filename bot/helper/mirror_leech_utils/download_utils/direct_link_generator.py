"""Provider dispatch; each site's URL rules live in its own module."""
from importlib import import_module
from urllib.parse import urlparse
from .resolver import Context,host_matches

PROVIDERS = ('mediafire','gofile','pixeldrain','mega','dropbox','google_drive','internet_archive',
             'huggingface','github','gitlab','bitbucket','yandex_disk','buzzheavier','krakenfiles','cyberdrop','bunkr',
             'youtube','vimeo','dailymotion','tiktok','instagram','facebook','twitter','reddit',
             'pinterest','twitch','soundcloud','bandcamp','bilibili','douyin','vk','rutube',
             'streamable','streamtape','mixdrop','xiaohongshu','imgur','flickr','imagebam','imgbox',
             'pixhost','imgbb','lensdump','catbox','redgifs','giphy','tumblr','deviantart','bluesky')


async def resolve(task,url,limit=1000):
    host=(urlparse(url).hostname or '').lower()
    async with Context(task,limit) as ctx:
        for name in dict.fromkeys(PROVIDERS):
            adapter=import_module('.providers.'+name,__package__)
            if host_matches(host,adapter.DOMAINS):
                await adapter.resolve(ctx,url)
                break
        else:
            from .providers import direct
            await direct.resolve(ctx,url)
        return ctx.result()


async def refresh_item(task,item):
    if item['engine']!='deferred':return item
    async with Context(task,1) as ctx:
        adapter=import_module('.providers.'+item['provider'],__package__)
        await adapter.resolve(ctx,item['url'])
        resolved=ctx.result()['items'][0]
        return {**resolved,'name':item['name'],'total':item.get('total') or resolved.get('total',0)}
