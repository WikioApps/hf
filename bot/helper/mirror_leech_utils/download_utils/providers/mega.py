"""Public MEGA files and folders, including complete keys from share links."""
from urllib.parse import urlparse, urlencode
from cryptography.hazmat.primitives.ciphers import modes
from bot.helper.ext_utils.files_utils import filename
from ..mega_crypto import decode,encode,file_key,attributes,decrypt

DOMAINS=('mega.nz','mega.co.nz')
API='https://g.api.mega.co.nz/cs'


def parse(url):
    p=urlparse(url);parts=p.path.strip('/').split('/');fragment=p.fragment
    if fragment.startswith('F!'):
        _,handle,key,*rest=fragment.split('!');return 'folder',handle,key,rest[0] if rest else ''
    if fragment.startswith('!'):
        _,handle,key=fragment.split('!');return 'file',handle,key,''
    key,*rest=fragment.split('/')
    if len(parts)!=2 or parts[0] not in ('file','folder') or not key:
        raise ValueError('Use a complete MEGA file or folder link, including # and its decryption key.')
    return parts[0],parts[1],key,rest[1] if len(rest)>1 else ''


async def call(ctx,command,folder=''):
    data=await ctx.json(API+'?'+urlencode({'id':1,**({'n':folder} if folder else {})}),method='POST',json_data=[command])
    item=data[0] if isinstance(data,list) and data else data
    if isinstance(item,int):
        raise ValueError('MEGA rejected the link or reached its transfer quota (code '+str(item)+').')
    if not isinstance(item,dict):raise ValueError('MEGA returned an invalid response.')
    return item


def node_info(node,key):
    for part in node.get('k','').split('/'):
        try:
            packed=decrypt(key,decode(part.split(':',1)[1]),modes.ECB())
            aes=file_key(packed)[0] if node['t']==0 else packed
            return packed,filename(attributes(aes,node['a'])['n'])
        except (ValueError,KeyError,IndexError):continue
    if node['t']==1:
        try:return key,filename(attributes(key,node['a'])['n'])
        except (ValueError,KeyError):pass
    raise ValueError('A MEGA folder entry could not be decrypted. Check the share key.')


async def resolve(ctx,url):
    kind,handle,key_text,selected=parse(url)
    key=decode(key_text)
    if kind=='file':
        aes,_,_=file_key(key)
        data=await call(ctx,{'a':'g','g':1,'p':handle})
        name=filename(attributes(aes,data['at'])['n'])
        ctx.add(url,name,data['s'],engine='mega',mega={'handle':handle,'key':key_text})
        return
    if len(key)!=16:raise ValueError('Invalid MEGA folder key.')
    data=await call(ctx,{'a':'f','c':1,'r':1},handle)
    nodes={n['h']:n for n in data.get('f',[])}
    decoded={n['h']:node_info(n,key) for n in nodes.values() if n['t'] in (0,1)}
    for ident,node in nodes.items():
        if node['t']!=0:continue
        parent=node.get('p');parents=[];seen={ident}
        while parent in nodes:
            if parent in seen:raise ValueError('MEGA returned a cyclic folder tree.')
            seen.add(parent);parents.insert(0,parent);parent=nodes[parent].get('p')
        if selected and selected!=ident and selected not in parents:continue
        packed,name=decoded[ident]
        name='/'.join([decoded[p][1] for p in parents]+[name])
        ctx.add(url,name,node.get('s',0),engine='mega',mega={'handle':ident,'folder':handle,'key':encode(packed)})
    ctx.title='MEGA folder'
