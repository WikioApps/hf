"""Telegram commands and mirror-leech style status views."""
import asyncio
import contextlib
from html import escape
import logging
from pathlib import Path
import shlex
import shutil
import time
from telethon import TelegramClient, events, Button, errors
from telethon.sessions import StringSession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
import psutil
from config import filename, safe_path
from core import Manager, TERMINAL, size, duration

LOG = logging.getLogger('hf.telegram')
HELP = '''<b>☁️ Hugging Face Mirror</b>

<code>/mirror URL</code> — download and upload to Hugging Face
Reply to a Telegram document/video/audio/photo with <code>/mirror</code>, or send the file directly.
Telegram message links are supported when the bot can access the chat.

<b>Options</b>
<code>-n "filename.zip"</code> — rename
<code>-unzip</code> or <code>-e</code> — extract, then upload members sequentially
<code>-s</code> — choose archive members (with -e)
<code>-p "folder"</code> — additional repository folder
One URL per line; options apply to that line.

<b>Commands</b>
/mirror or /m — start a transfer
/status or /s — live tasks; /status me for yours
/select ID — choose archive members
/cancel ID — cancel your task
/retry ID — retry retained files after failure/restart
/history — recent tasks and results
/clean ID — delete retained local data for a finished task
/stats — CPU, RAM, disk and queue usage
/help — this guide

Hugging Face is the only upload destination. Each task gets a unique folder to prevent overwrites. Download, extraction and upload have separate progress and ETA. Hashing and commit finalization have no fabricated percentage.

<b>Sources</b>
Public direct HTTP/HTTPS, Telegram media, public Google Drive files, Dropbox, Pixeldrain, MediaFire and single-file HTTP formats resolved by yt-dlp. Host login/CAPTCHA/DRM, torrents, NZB and formats requiring FFmpeg are not supported. RAR files can be uploaded intact; extraction supports ZIP, TAR variants and 7Z.
'''


def parse_line(line):
    parts = shlex.split(line)
    source, opts = None, {'extract':False, 'select':False, 'prefix':''}
    i = 0
    while i < len(parts):
        p = parts[i]
        if p in ('-e','-unzip'): opts['extract'] = True
        elif p == '-s': opts['select'] = True
        elif p in ('-n','-p'):
            i += 1
            if i >= len(parts): raise ValueError(f'{p} needs a value')
            if p == '-n': opts.update(name=filename(parts[i]), rename=True)
            else: opts['prefix'] = safe_path(parts[i])
        elif p.startswith(('http://','https://')) and source is None: source = p
        else: raise ValueError('Unknown option or invalid URL: use /help')
        i += 1
    if opts['select'] and not opts['extract']: raise ValueError('-s requires -e or -unzip')
    return source, opts


def task_text(j):
    known = j.phase in ('Download','Extract','Upload')
    percentage = min(100, 100*j.done/j.total) if j.total else None
    bar = '■' * int((percentage or 0)*12/100) + '□' * (12-int((percentage or 0)*12/100))
    elapsed = time.time()-j.created
    text = f'<b><i>{escape(j.name[:160])}</i></b>\n<b>Task By</b> <a href="tg://user?id={j.user_id}">{j.user_id}</a>\n'
    if j.current: text += f'┖ <b>Sub Name</b> → {escape(j.current[:120])}\n'
    if known:
        text += f'┟ [{bar}] <i>{f"{percentage:.2f}%" if percentage is not None else "unknown size"}</i>\n'
        text += f'┠ <b>Processed</b> → {size(j.done)} of {size(j.total) if j.total else "?"}\n'
        text += f'┠ <b>Speed</b> → {size(j.speed)}/s\n'
        text += f'┠ <b>Time</b> → {duration(j.eta)} of {duration(elapsed+j.eta) if j.eta is not None else '-'} ({duration(elapsed)})\n'
    else: text += f'┠ <b>Size</b> → {size(j.total)} | <b>Elapsed</b> → {duration(elapsed)}\n'
    text += f'┠ <b>Status</b> → {j.phase}\n┠ <b>Engine</b> → {escape(j.engine)}\n'
    text += f'┠ <b>In Mode</b> → Download\n┠ <b>Out Mode</b> → Hugging Face\n'
    if j.members: text += f'┠ <b>Committed</b> → {len(j.uploaded)} / {len(j.selected)} files\n'
    return text + f'┖ <b>Stop</b> → <code>/cancel {j.id}</code>\n'


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = TelegramClient(str(cfg.data / 'bot'), cfg.api_id, cfg.api_hash,
                                     request_retries=3, connection_retries=5, flood_sleep_threshold=30)
        self.user_client = None
        self.manager = None
        self.views = {}
        self.tick = 0.0
        self.started = time.time()
        self.stopping = False
        self.loop_task = None
        self.fatal = False
    async def start(self):
        await self.client.start(bot_token=self.cfg.bot_token)
        me = await self.client.get_me()
        if me.id != int(self.cfg.bot_token.split(':', 1)[0]):
            raise ValueError('DATA_DIR holds a session for a different bot; use a separate DATA_DIR')
        if self.cfg.user_session:
            self.user_client = TelegramClient(StringSession(self.cfg.user_session), self.cfg.api_id, self.cfg.api_hash)
            await self.user_client.connect()
            if not await self.user_client.is_user_authorized(): raise ValueError('USER_SESSION_STRING is not authorized')
        self.manager = Manager(self.cfg, self.client, self.user_client, self.notify)
        self.client.add_event_handler(self.message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self.callback, events.CallbackQuery())
        commands = [('start','Welcome'),('help','Commands and supported sources'),('mirror','Upload a link or replied file'),
                    ('status','Live transfer status'),('stats','System statistics'),('history','Recent tasks'),
                    ('cancel','Cancel a task'),('retry','Retry a task'),('select','Select archive files'),('clean','Remove retained files')]
        await self.client(SetBotCommandsRequest(BotCommandScopeDefault(), '', [BotCommand(*c) for c in commands]))
        self.tick = time.monotonic()
        self.loop_task = asyncio.create_task(self.status_loop(), name='status-supervisor')
    async def notify(self, job, text):
        await self.client.send_message(job.chat_id, text, parse_mode=None, link_preview=False)
    def permitted(self, user): return user in self.cfg.allowed_users
    def owned(self, ident, user):
        job = self.manager.jobs.get(ident)
        if not job or (job.user_id != user and user != self.cfg.owner_id): raise ValueError('Task not found or not yours')
        return job
    def stats(self):
        disk = shutil.disk_usage(self.cfg.data)
        return f'⌬ <b><u>Bot Stats</u></b>\n┟ <b>CPU</b> → {psutil.cpu_percent()}% | <b>F</b> → {size(disk.free)}\n┖ <b>RAM</b> → {psutil.virtual_memory().percent}% | <b>UP</b> → {duration(time.time()-self.started)}'
    async def message(self, event):
        if not self.permitted(event.sender_id): return
        try:
            text = event.raw_text or ''
            head, _, rest = text.partition(' ')
            cmd = head.split('@')[0].lower()
            # Preserve multiline command content.
            if '\n' in head:
                cmd = head.split('\n')[0].split('@')[0].lower(); rest = text.split('\n',1)[1]
            if cmd in ('/start','/help'):
                await event.reply(HELP, parse_mode='html', link_preview=False)
            elif cmd in ('/stats',): await event.reply(self.stats(), parse_mode='html')
            elif cmd in ('/status','/s'):
                await self.show_status(event, rest.strip() == 'me')
            elif cmd == '/history':
                jobs = [j for j in self.manager.jobs.values() if j.user_id == event.sender_id or event.sender_id == self.cfg.owner_id]
                out = []
                for j in sorted(jobs,key=lambda j:j.created,reverse=True)[:10]:
                    out.append(f'{j.id} | {j.phase} | {j.name}\n' + (self.manager.repo_url(j) if j.uploaded else j.error))
                await event.reply(('\n\n'.join(out) or 'No task history.')[:3900], parse_mode=None, link_preview=False)
            elif cmd in ('/cancel','/retry','/clean','/select'):
                job = self.owned(rest.strip(), event.sender_id)
                if cmd == '/cancel': await self.manager.cancel(job)
                elif cmd == '/retry': await self.manager.retry(job); await event.reply('Retry queued: '+job.id)
                elif cmd == '/clean':
                    if job.phase not in TERMINAL: raise ValueError('Cancel the running task first')
                    shutil.rmtree(job.directory); del self.manager.jobs[job.id]
                    await event.reply('Local task files removed. Hugging Face files are unchanged.')
                else:
                    if job.phase != 'Select': raise ValueError('This task is not waiting for selection')
                    text, buttons = self.selection(job, 0)
                    await event.reply(text, buttons=buttons, parse_mode='html')
            elif cmd in ('/mirror','/m','/upload','/u') or event.message.file:
                lines = rest.splitlines() if cmd.startswith('/') else ['']
                reply = await event.get_reply_message() if event.is_reply else None
                for line in lines or ['']:
                    source, options = parse_line(line)
                    source = source or (event.message if event.message.file else reply)
                    if source and not isinstance(source,str) and not source.file:
                        source = (source.raw_text or '').strip()
                    if not source: raise ValueError('Send a URL or reply to a Telegram file with /mirror')
                    job = self.manager.add(event.sender_id,event.chat_id,event.id,source,**options)
                    await event.reply('Queued: '+job.id)
                await self.show_status(event, False)
        except (ValueError, errors.RPCError) as exc:
            msg = str(exc) if type(exc) is ValueError else type(exc).__name__ + ': Telegram request failed'
            await event.reply(escape(msg), parse_mode='html')
        except Exception:
            LOG.exception('command_failed')
            await event.reply('Command failed; check application logs.')
    async def show_status(self, event, only_me):
        key = (event.chat_id, event.sender_id)
        old = self.views.get(key)
        if old:
            with contextlib.suppress(errors.RPCError): await old['message'].delete()
        view = {'user':event.sender_id, 'chat':event.chat_id, 'me':only_me, 'page':0,'filter':'All','last':'','after':0}
        text, buttons = self.render(view)
        view['message'] = await event.reply(text,buttons=buttons,parse_mode='html',link_preview=False)
        view['last'] = text; self.views[key] = view
    def render(self, view):
        jobs = [j for j in self.manager.active() if j.chat_id == view['chat'] and
                (j.user_id == view['user'] or (view['user'] == self.cfg.owner_id and not view['me']))]
        if view.get('overview'):
            phases = sorted({j.phase for j in jobs})
            text = '<b>Tasks Overview</b>\n' + '\n'.join(f'<b>{p}:</b> {sum(j.phase == p for j in jobs)}' for p in phases)
            for phase in ('Download','Upload'):
                text += f'\n<b>Total {phase} Speed:</b> {size(sum(j.speed for j in jobs if j.phase == phase))}/s'
            return text+'\n\n'+self.stats(), [[Button.inline('Back',f'v:{view["user"]}:ref')]]
        if view['filter'] != 'All': jobs = [j for j in jobs if j.phase == view['filter']]
        pages = max(1,(len(jobs)+2)//3); view['page'] %= pages
        start = view['page']*3
        text = '\n'.join(task_text(j) for j in jobs[start:start+3]) or '<b>No active tasks in this view.</b>\n'
        text += f'\n<b>Page:</b> {view["page"]+1}/{pages} | <b>Tasks:</b> {len(jobs)}\n' + self.stats()
        prefix = f'v:{view["user"]}:'
        buttons = [[Button.inline('📜 TStats',prefix+'ov')], [Button.inline('≪',prefix+'prev'), Button.inline('♻️ Refresh',prefix+'ref'),Button.inline('≫',prefix+'next')],
                   [Button.inline(x,prefix+x) for x in ('All','Download','Upload','QueueDl','QueueUp')]]
        buttons.append([Button.inline(x,prefix+x) for x in ('Extract','Select','Prepare','Commit')])
        return text, buttons
    def selection(self, job, page):
        pages = max(1,(len(job.members)+7)//8); page %= pages
        text = f'<b>Archive selection</b> — {len(job.selected)}/{len(job.members)} selected\nPage {page+1}/{pages}'
        buttons = []
        for i in range(page*8,min(len(job.members),page*8+8)):
            name,length = job.members[i]
            buttons.append([Button.inline(('✅ ' if i in job.selected else '⬜ ') + name[:45] + ' '+size(length),f'a:{job.id}:{page}:{i}')])
        buttons += [[Button.inline('≪',f'a:{job.id}:{page-1}:page'),Button.inline('≫',f'a:{job.id}:{page+1}:page')],
                    [Button.inline('All',f'a:{job.id}:{page}:all'),Button.inline('None',f'a:{job.id}:{page}:none'),Button.inline('Upload selected',f'a:{job.id}:{page}:go')]]
        return text,buttons
    async def callback(self, event):
        if not self.permitted(event.sender_id): return await event.answer('Not authorized',alert=True)
        try:
            parts = event.data.decode().split(':')
            if parts[0] == 'v':
                if int(parts[1]) != event.sender_id: return await event.answer('Open your own /status view',alert=True)
                view = self.views.get((event.chat_id,event.sender_id))
                if not view or view['message'].id != event.message_id: return await event.answer('Open /status again')
                action = parts[2]
                view['overview'] = action == 'ov'
                if action in ('next','prev'): view['page'] += 1 if action == 'next' else -1
                elif action not in ('ref','ov'): view['filter'] = action; view['page'] = 0
                text, buttons = self.render(view); view['last'] = text
            else:
                job = self.owned(parts[1],event.sender_id)
                if job.phase != 'Select': return await event.answer('Selection has closed')
                page, action = int(parts[2]),parts[3]
                if action == 'go':
                    if not job.selected: return await event.answer('Select at least one file',alert=True)
                    job.save(); job.selection.set(); return await event.edit('Selected files queued for upload.')
                elif action == 'all': job.selected = set(range(len(job.members)))
                elif action == 'none': job.selected.clear()
                elif action != 'page':
                    i = int(action)
                    if not 0 <= i < len(job.members): raise ValueError('Invalid member')
                    if i in job.selected: job.selected.remove(i)
                    else: job.selected.add(i)
                text, buttons = self.selection(job,page)
            with contextlib.suppress(errors.MessageNotModifiedError):
                await event.edit(text,buttons=buttons,parse_mode='html')
            await event.answer()
        except (ValueError,KeyError,IndexError): await event.answer('Invalid or expired action',alert=True)
    async def status_loop(self):
        offline = None
        while not self.stopping:
            self.tick = time.monotonic()
            if not self.client.is_connected():
                offline = offline or self.tick
                if self.tick-offline > 60: raise RuntimeError('Telegram disconnected for over 60 seconds')
            else: offline = None
            for key,view in list(self.views.items()):
                if time.monotonic() < view['after']: continue
                text, buttons = self.render(view)
                if text == view['last']: continue
                try:
                    await view['message'].edit(text,buttons=buttons,parse_mode='html',link_preview=False)
                    view['last'] = text
                    if not self.manager.active(): self.views.pop(key,None)
                except errors.FloodWaitError as e: view['after'] = time.monotonic()+e.seconds+1
                except errors.MessageNotModifiedError: pass
                except errors.RPCError: self.views.pop(key,None)
            await asyncio.sleep(self.cfg.interval)
    def healthy(self):
        return bool(self.loop_task and not self.loop_task.done() and self.client.is_connected()
                    and time.monotonic()-self.tick < max(30,self.cfg.interval*4) and not self.stopping)
    async def close(self):
        self.stopping = True
        if self.loop_task: self.loop_task.cancel(); await asyncio.gather(self.loop_task,return_exceptions=True)
        if self.manager: await self.manager.close()
        if self.user_client: await self.user_client.disconnect()
        await self.client.disconnect()
