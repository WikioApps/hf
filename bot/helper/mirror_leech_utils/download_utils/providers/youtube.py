"""Public youtube links through the maintained yt-dlp extractor."""
DOMAINS = ('youtube.com', 'youtu.be')


async def resolve(ctx,url):
    await ctx.media(url,'video')
