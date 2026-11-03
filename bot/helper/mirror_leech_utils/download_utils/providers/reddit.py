"""Public reddit links through the maintained yt-dlp extractor."""
DOMAINS = ('reddit.com', 'redd.it')


async def resolve(ctx,url):
    await ctx.media(url,'media')
