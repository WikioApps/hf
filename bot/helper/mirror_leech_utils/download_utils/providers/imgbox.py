"""Public imgbox links through the maintained gallery-dl extractor."""
DOMAINS = ('imgbox.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
