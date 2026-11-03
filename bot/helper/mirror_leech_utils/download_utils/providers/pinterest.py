"""Public pinterest links through the maintained yt-dlp extractor."""
DOMAINS = ('pinterest.com', 'pin.it')


async def resolve(ctx,url):
    await ctx.media(url,'media')
