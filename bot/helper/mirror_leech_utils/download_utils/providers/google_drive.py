DOMAINS = ('drive.google.com','docs.google.com')


async def resolve(ctx,url):
    if '/folders/' in url:
        await ctx.media(url,'drive_folder')
    else:
        ctx.add(url,'Google Drive file',engine='gdown')
