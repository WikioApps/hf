"""Public tumblr links through the maintained gallery-dl extractor."""
DOMAINS = ('tumblr.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
