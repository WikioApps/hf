"""Public Gofile content using an automatically created guest session."""
import asyncio
import hashlib
import time
from urllib.parse import urlparse, urlencode
from bot.helper.ext_utils.files_utils import filename
from bot.helper.ext_utils.log_utils import register_secrets
from ..resolver import USER_AGENT

DOMAINS = ('gofile.io',)
_guest = None
_guest_at = 0
_lock = asyncio.Lock()


async def guest(ctx):
    global _guest,_guest_at
    async with _lock:
        if not _guest or time.monotonic()-_guest_at>3600:
            data = await ctx.json('https://api.gofile.io/accounts',method='POST')
            if data.get('status')!='ok':raise ValueError('Gofile could not create a guest session. Retry later.')
            _guest = data['data']['token'];_guest_at=time.monotonic()
            register_secrets(_guest)
        return _guest


async def resolve(ctx,url):
    parts = urlparse(url).path.strip('/').split('/')
    if len(parts)!=2 or parts[0]!='d':
        ctx.add(url);return
    token = await guest(ctx)
    # Public website-session calculation; see docs/sources.md for protocol reference.
    material = f'{USER_AGENT}::en-US::{token}::{int(time.time())//14400}::12af056dacea0b'
    headers = {'Authorization':'Bearer '+token,'X-Website-Token':hashlib.sha256(material.encode()).hexdigest(),
               'X-BL':'en-US','Origin':'https://gofile.io','Referer':'https://gofile.io/'}
    pending,seen = [(parts[1],'')],set()
    while pending:
        ident,prefix = pending.pop(0)
        if ident in seen:continue
        seen.add(ident)
        if len(seen)>ctx.limit:raise ValueError('Gofile folder tree exceeds the collection limit.')
        for page in range(1,ctx.limit+2):
            query = urlencode({'page':page,'pageSize':100,'sortField':'createTime','sortDirection':1})
            data = await ctx.json('https://api.gofile.io/contents/'+ident+'?'+query,headers=headers)
            if data.get('status')!='ok':
                raise ValueError('Gofile refused public access ('+str(data.get('status','unknown'))+'). The link may be restricted, rate-limited, or its website API may have changed.')
            node = data['data']
            if node.get('canAccess') is False or node.get('passwordStatus') == 'passwordRequired':
                raise ValueError('Gofile requires a password or account for this link.')
            children = node.get('children',{})
            items = [node] if node.get('type')=='file' else list(children.values())
            for child in items:
                name = prefix+filename(child.get('name') or child['id'])
                if child.get('type')=='folder':pending.append((child['id'],name+'/'))
                elif child.get('link'):
                    ctx.add(child['link'],name,child.get('size',0),headers={'Cookie':'accountToken='+token,'User-Agent':USER_AGENT})
            if not data.get('metadata',{}).get('hasNextPage'):break
        else:raise ValueError('Gofile pagination exceeds the collection limit.')
    ctx.title = 'Gofile collection'
