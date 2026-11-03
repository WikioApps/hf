"""Public douyin links through the maintained yt-dlp extractor."""
DOMAINS = ('douyin.com',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
