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
