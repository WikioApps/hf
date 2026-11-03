"""Public streamtape links through the maintained yt-dlp extractor."""
DOMAINS = ('streamtape.com', 'streamtape.to')


async def resolve(ctx,url):
    await ctx.media(url,'video')
