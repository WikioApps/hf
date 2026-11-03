"""Public dailymotion links through the maintained yt-dlp extractor."""
DOMAINS = ('dailymotion.com', 'dai.ly')


async def resolve(ctx,url):
    await ctx.media(url,'video')
