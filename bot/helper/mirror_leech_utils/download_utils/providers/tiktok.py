"""Public tiktok links through the maintained yt-dlp extractor."""
DOMAINS = ('tiktok.com',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
