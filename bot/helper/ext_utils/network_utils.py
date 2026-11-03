import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

async def public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use a public HTTP or HTTPS URL.')
    try:
        addresses = [ipaddress.ip_address(parsed.hostname)]
    except ValueError:
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM), 20)
        addresses = [ipaddress.ip_address(r[4][0]) for r in records]
    if not addresses or any(not a.is_global for a in addresses):
        raise ValueError('Private and local network URLs are not allowed.')
    return url


def public_url_sync(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTP(S) downloads are supported.')
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Private and local network URLs are not allowed.')
    return url


def guard_worker_requests():
    """Validate extractor requests and redirects without loading browser credentials."""
    import requests
    import urllib.request
    from yt_dlp.networking.common import RequestDirector
    original_send=requests.Session.send
    def send(self,request,**kwargs):
        public_url_sync(request.url)
        return original_send(self,request,**kwargs)
    requests.Session.send=send
    director_send=RequestDirector.send
    def media_send(self,request):
        public_url_sync(request.url)
        return director_send(self,request)
    RequestDirector.send=media_send
    redirect=urllib.request.HTTPRedirectHandler.redirect_request
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        public_url_sync(newurl)
        return redirect(self,request,fp,code,msg,headers,newurl)
    urllib.request.HTTPRedirectHandler.redirect_request=redirect_request
