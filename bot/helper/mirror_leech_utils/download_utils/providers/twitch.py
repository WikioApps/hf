"""Public twitch links through the maintained yt-dlp extractor."""
DOMAINS = ('twitch.tv',)


async def resolve(ctx,url):
    await ctx.media(url,'video')
