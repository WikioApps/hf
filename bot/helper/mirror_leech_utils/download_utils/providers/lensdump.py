"""Public lensdump links through the maintained gallery-dl extractor."""
DOMAINS = ('lensdump.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
