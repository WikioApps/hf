"""Public pixhost links through the maintained gallery-dl extractor."""
DOMAINS = ('pixhost.to',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
