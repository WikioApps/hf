"""Public mixdrop links through the maintained yt-dlp extractor."""
DOMAINS = ('mixdrop.co', 'mixdrop.to', 'mixdrop.ag')


async def resolve(ctx,url):
    await ctx.media(url,'video')
