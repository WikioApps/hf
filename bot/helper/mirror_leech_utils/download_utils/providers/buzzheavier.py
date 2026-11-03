from urllib.parse import urljoin
from bot.helper.ext_utils.files_utils import filename
DOMAINS = ('buzzheavier.com','bzzhr.co','bzzhr.to','fuckingfast.net')


async def resolve(ctx,url):
    soup = await ctx.soup(url)
    button = soup.select_one('[hx-get]')
    endpoint = urljoin(url,button['hx-get']) if button else url.rstrip('/')+'/download'
    result = await ctx.request(endpoint,method='HEAD',headers={'HX-Request':'true','HX-Current-URL':url})
    target = result['headers'].get('hx-redirect')
    if not target:raise ValueError('This host did not provide a public download link.')
    title=soup.select_one('.file-name')
    ctx.add(urljoin(url,target),filename(title.get_text(strip=True)) if title else '')
