"""Public imgur links through the maintained gallery-dl extractor."""
DOMAINS = ('imgur.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
