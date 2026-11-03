"""Public twitter links through the maintained yt-dlp extractor."""
DOMAINS = ('twitter.com', 'x.com')


async def resolve(ctx,url):
    await ctx.media(url,'video')
