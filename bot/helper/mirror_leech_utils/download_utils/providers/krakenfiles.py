from urllib.parse import urljoin
DOMAINS = ('krakenfiles.com',)


async def resolve(ctx,url):
    soup=await ctx.soup(url)
    form=soup.select_one('form#dl-form');token=soup.select_one('input#dl-token')
    if not form or not token:raise ValueError('Krakenfiles download form is unavailable.')
    data=await ctx.json(urljoin(url,form.get('action','')),method='POST',data={'token':token.get('value','')})
    if data.get('status')!='ok' or not data.get('url'):raise ValueError('Krakenfiles refused this download.')
    ctx.add(data['url'])
