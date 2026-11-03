"""Public rutube links through the maintained yt-dlp extractor."""
DOMAINS = ('rutube.ru',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
