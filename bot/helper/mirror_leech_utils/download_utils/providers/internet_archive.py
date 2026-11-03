from urllib.parse import urlparse, quote
from bot.helper.ext_utils.files_utils import safe_path
DOMAINS = ('archive.org',)


async def resolve(ctx,url):
    parts=urlparse(url).path.strip('/').split('/')
    if len(parts)==2 and parts[0] in ('details','download'):
        data=await ctx.json('https://archive.org/metadata/'+parts[1])
        ctx.title=str(data.get('metadata',{}).get('title') or parts[1])
        for item in data.get('files',[]):
            if item.get('source')!='original' or item.get('private'):continue
            name=safe_path(item['name'])
            if name.endswith(('_meta.xml','_files.xml','.sqlite','.torrent')):continue
            ctx.add('https://archive.org/download/'+quote(parts[1])+'/'+quote(name,safe='/'),name,item.get('size',0))
    else:
        from .direct import resolve
        await resolve(ctx,url)
