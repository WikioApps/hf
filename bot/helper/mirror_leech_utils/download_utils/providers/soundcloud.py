"""Public soundcloud links through the maintained yt-dlp extractor."""
DOMAINS = ('soundcloud.com',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
