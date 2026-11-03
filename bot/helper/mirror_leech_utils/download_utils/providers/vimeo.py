"""Public vimeo links through the maintained yt-dlp extractor."""
DOMAINS = ('vimeo.com',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
