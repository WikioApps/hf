"""Public facebook links through the maintained yt-dlp extractor."""
DOMAINS = ('facebook.com', 'fb.watch')


async def resolve(ctx,url):
    await ctx.media(url,'video')
