"""Public vk links through the maintained yt-dlp extractor."""
DOMAINS = ('vk.com', 'vkvideo.ru')


async def resolve(ctx,url):
    await ctx.media(url,'video')
