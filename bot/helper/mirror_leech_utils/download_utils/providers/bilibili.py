"""Public bilibili links through the maintained yt-dlp extractor."""
DOMAINS = ('bilibili.com', 'b23.tv')


async def resolve(ctx,url):
    await ctx.media(url,'video')
