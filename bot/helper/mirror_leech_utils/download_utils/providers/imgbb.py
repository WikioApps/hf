"""Public imgbb links through the maintained gallery-dl extractor."""
DOMAINS = ('ibb.co', 'imgbb.com')


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
