"""Public instagram links through the maintained yt-dlp extractor."""
DOMAINS = ('instagram.com',)


async def resolve(ctx,url):
    await ctx.media(url,'media')
