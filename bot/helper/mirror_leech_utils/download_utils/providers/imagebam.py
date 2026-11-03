"""Public imagebam links through the maintained gallery-dl extractor."""
DOMAINS = ('imagebam.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
