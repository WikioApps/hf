"""Speedtest.net measurements and the provider's real share image."""
import asyncio
from html import escape
from io import BytesIO
import time
from urllib.parse import urlparse
import httpx
from bot.helper.ext_utils.format_utils import size
from bot.helper.ext_utils.process_utils import run_worker
from bot.helper.mirror_leech_utils.status_utils.task_status import TaskStatus


def result_text(result):
    server = result.get('server') or {}
    def value(item):return escape(str(item if item is not None else 'Unavailable')[:80])
    def transferred(key):
        amount=result.get(key)
        return size(amount) if amount is not None else None
    sections = [
        ('SPEEDTEST INFO',[
            ('Upload',size(result['upload']/8)+'/s'),('Download',size(result['download']/8)+'/s'),
            ('Ping',str(result['ping'])+' ms'),('Time',result.get('timestamp')),
            ('Data Sent',transferred('bytes_sent')),('Data Received',transferred('bytes_received'))]),
        ('SPEEDTEST SERVER',[
            ('Name',server.get('name')),('Country',str(server.get('country',''))+', '+str(server.get('cc',''))),
            ('Sponsor',server.get('sponsor')),('Latency',server.get('latency')),
            ('Latitude',server.get('lat')),('Longitude',server.get('lon'))])]
    blocks=[]
    for title,rows in sections:
        lines=['➲ <b><i>'+title+'</i></b>']
        for index,(label,item) in enumerate(rows):
            lines.append(('┖' if index==len(rows)-1 else '┠')+' <b>'+label+':</b> <code>'+value(item)+'</code>')
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


def share_url(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or parsed.hostname not in ('speedtest.net','www.speedtest.net') or parsed.username:
        raise ValueError('Speedtest returned an unexpected image address.')
    return parsed._replace(scheme='https').geturl()


async def result_image(url):
    async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
        for _ in range(4):
            url=share_url(url)
            async with client.stream('GET',url) as response:
                if response.is_redirect:
                    url=str(response.url.join(response.headers['location']))
                    continue
                response.raise_for_status()
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>4*1024**2:raise ValueError('Speedtest result image is too large.')
                if not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8')):
                    raise ValueError('Speedtest did not return a result image.')
                image=BytesIO(data);image.name='speedtest.png' if data.startswith(b'\x89PNG') else 'speedtest.jpg'
                return image
    raise ValueError('Speedtest result image redirected too many times.')


async def start(runtime,event):
    if event.sender_id != runtime.config.owner_id or not event.is_private:
        raise ValueError('Owner only. Use /speedtest in a private chat.')
    if getattr(runtime,'speedtest_running',False):raise ValueError('A speed test is already running.')
    if runtime.speedtest_at and time.monotonic()-runtime.speedtest_at<300:
        raise ValueError('Wait five minutes between speed tests.')
    runtime.speedtest_running=True
    runtime.speedtest_at=time.monotonic()
    try:
        status=await event.reply('<i>Initiating Speedtest...</i>',parse_mode='html')
    except Exception:
        runtime.speedtest_running=False;runtime.speedtest_at=0
        raise
    task=asyncio.create_task(measure(runtime,event,status))
    runtime.auxiliary.add(task)
    task.add_done_callback(runtime.auxiliary.discard)


async def measure(runtime,event,status):
    try:
        task=TaskStatus(event.sender_id,event.chat_id,event.id,{}, {}, {},runtime.config.data)
        result=await run_worker(task,'speedtest',[],timeout=180)
        text=result_text(result)
        runtime.logger.info('Speedtest complete download_bps=%s upload_bps=%s ping_ms=%s',result['download'],result['upload'],result['ping'])
        if result.get('share'):
            try:
                photo=await result_image(result['share'])
                await runtime.client.send_file(event.chat_id,photo,caption=text,parse_mode='html',force_document=False)
                await status.delete()
                return
            except Exception:
                runtime.logger.exception('Speedtest image delivery failed; keeping measured text result')
        await status.edit(text,parse_mode='html')
    except asyncio.CancelledError:
        try:
            await status.edit('Speedtest cancelled.')
        except Exception:
            runtime.logger.warning('Could not clear cancelled Speedtest message')
        raise
    except Exception as error:
        runtime.speedtest_at=0
        runtime.logger.exception('Speedtest failed')
        await status.edit('<b>ERROR:</b> <i>Cannot complete Speedtest right now ('+escape(type(error).__name__)+'). Try again later. Details are in /logs.</i>',parse_mode='html')
    finally:
        runtime.speedtest_running=False
