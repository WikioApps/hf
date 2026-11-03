"""Public bluesky links through the maintained gallery-dl extractor."""
DOMAINS = ('bsky.app',)


async def resolve(ctx,url):
    await ctx.media(url,'gallery')
