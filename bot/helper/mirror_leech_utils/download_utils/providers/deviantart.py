"""Public deviantart links through the maintained gallery-dl extractor."""
DOMAINS = ('deviantart.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
