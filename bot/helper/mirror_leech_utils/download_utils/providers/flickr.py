"""Public flickr links through the maintained gallery-dl extractor."""
DOMAINS = ('flickr.com',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
