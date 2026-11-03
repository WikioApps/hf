"""Public bunkr links through the maintained gallery-dl extractor."""
DOMAINS = ('bunkr.cr', 'bunkr.si', 'bunkr.ru', 'bunkr.la', 'bunkr.ac', 'bunkr.is', 'bunkr.ci', 'bunkr.fi', 'bunkr.sk')


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
