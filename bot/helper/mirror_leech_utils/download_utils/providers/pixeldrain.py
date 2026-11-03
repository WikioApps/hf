from urllib.parse import urlparse
from bot.helper.ext_utils.files_utils import filename

DOMAINS = ('pixeldrain.com','pixeldrain.net')


async def resolve(ctx,url):
    parts = urlparse(url).path.strip('/').split('/')
    if len(parts)<2:raise ValueError('Use a Pixeldrain file or list link.')
    if parts[0] == 'l':
        data = await ctx.json('https://pixeldrain.com/api/list/'+parts[1])
        ctx.title = data.get('title','Pixeldrain list')
        items = data.get('files',[])
    elif parts[0] == 'u':
        items = [await ctx.json('https://pixeldrain.com/api/file/'+parts[1]+'/info')]
    elif parts[:2] == ['api','file']:
        ctx.add(url);return
    else:raise ValueError('Use a Pixeldrain /u/ file or /l/ list link.')
    for item in items:
        if item.get('success') is False:raise ValueError('Pixeldrain file is unavailable or rate-limited.')
        ctx.add('https://pixeldrain.com/api/file/'+item['id']+'?download',filename(item['name']),item.get('size',0))
