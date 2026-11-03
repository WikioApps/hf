"""Public redgifs links through the maintained yt-dlp extractor."""
DOMAINS = ('redgifs.com',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
