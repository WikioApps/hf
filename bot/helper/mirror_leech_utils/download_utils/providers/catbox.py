DOMAINS = ("catbox.moe","litterbox.catbox.moe")


async def resolve(ctx,url):
    from .direct import resolve
    await resolve(ctx,url)
