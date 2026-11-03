"""Public Xiaohongshu posts supported by yt-dlp."""
DOMAINS = ("xiaohongshu.com","xhslink.com")


async def resolve(ctx,url):
    await ctx.media(url,"video")
