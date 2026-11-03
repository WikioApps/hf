"""Public bandcamp links through the maintained yt-dlp extractor."""
DOMAINS = ('bandcamp.com',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
