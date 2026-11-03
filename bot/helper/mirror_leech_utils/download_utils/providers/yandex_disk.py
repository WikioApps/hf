"""Anonymous Yandex Disk shares through its documented public resources API."""
from urllib.parse import urlencode
from bot.helper.ext_utils.files_utils import filename
DOMAINS=('disk.yandex.com','disk.yandex.ru','disk.yandex.net','yadi.sk')
API='https://cloud-api.yandex.net/v1/disk/public/resources'


async def resolve(ctx,url):
    async def metadata(path='',offset=0):
        return await ctx.json(API+'?'+urlencode({'public_key':url,'path':path,'limit':100,'offset':offset}))
    root=await metadata()
    pending=[(root,'')];seen=set()
    while pending:
        node,prefix=pending.pop(0)
        path=node.get('path','/')
        if path in seen:continue
        seen.add(path)
        if len(seen)>ctx.limit*2:raise ValueError('Yandex folder exceeds the collection limit.')
        if node.get('type')=='file':
            href=await ctx.json(API+'/download?'+urlencode({'public_key':url,'path':path}))
            if not href.get('href'):raise ValueError('Yandex Disk did not provide a public download.')
            ctx.add(href['href'],prefix+filename(node['name']),node.get('size',0))
            continue
        if node.get('type')!='dir':raise ValueError('Yandex Disk public share is unavailable.')
        subprefix=prefix+(filename(node['name'])+'/' if node is not root else '')
        embedded=node.get('_embedded',{})
        while True:
            items=embedded.get('items',[])
            pending.extend((item,subprefix) for item in items)
            offset=int(embedded.get('offset',0))+len(items)
            if not items or offset>=int(embedded.get('total',0)):break
            embedded=(await metadata(path,offset)).get('_embedded',{})
    ctx.title=root.get('name','Yandex Disk')
