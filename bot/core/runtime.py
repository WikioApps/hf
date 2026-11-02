import asyncio
from html import escape
import logging
import os
import time
import uuid
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer, InputPeerUser
from bot.helper.ext_utils.db_handler import Database
from bot.helper.ext_utils.task_manager import TaskManager
from bot.helper.ext_utils.format_utils import size, duration
from bot.helper.telegram_helper.bot_commands import COMMANDS
from bot.modules.status import StatusViews
from bot.modules.users_settings import SettingsMenu


class Runtime:
    def __init__(self, config):
        self.config = config
        self.db = Database(config)
        self.settings = {}
        self.manager = TaskManager(self)
        self.status = StatusViews(self)
        self.menus = SettingsMenu(self)
        self.logger = logging.getLogger('hfbot')
        self.started = time.time()
        self.instance = uuid.uuid4().hex
        self.client = None
        self.monitor = None
        self.ready = False
        self.db_checked = 0
        self.shutdown_requested = asyncio.Event()
        self.auxiliary = set()
        self.offers = {}
        self.speedtest_at = 0
        self.username = ''

    async def start(self):
        self.config.data.mkdir(parents=True,exist_ok=True)
        os.chmod(self.config.data,0o700)
        await self.db.connect()
        await self.db.lease(self.instance)
        await self.reload_settings()
        await self.db.user(self.config.owner_id)
        record = await self.db.db.telegram.find_one({'_id':self.db.scope}) or {}
        session = StringSession(self.db.open(record.get('session','')))
        self.client = TelegramClient(session,self.config.api_id,self.config.api_hash,
                                     request_retries=3,connection_retries=5)
        from bot.modules.router import Router
        self.router = Router(self)
        self.client.add_event_handler(self.router.message,events.NewMessage(incoming=True))
        self.client.add_event_handler(self.router.callback,events.CallbackQuery())
        await asyncio.wait_for(self.client.start(bot_token=self.config.bot_token),60)
        me = await self.client.get_me()
        if str(me.id) != self.config.bot_id:
            raise ValueError('Stored Telegram session belongs to another bot.')
        self.username = me.username or ''
        await self.db.db.telegram.replace_one({'_id':self.db.scope},{'_id':self.db.scope,
            'session':self.db.seal(self.client.session.save())},upsert=True)
        await self.register_commands()
        await self.manager.recover()
        self.ready = True
        self.db_checked = time.monotonic()
        self.monitor = asyncio.create_task(self.supervise())
        self.logger.info('Bot connected; commands registered; MongoDB ready')

    async def register_commands(self):
        admin = {'botsettings','users','speedtest'}
        commands = [BotCommand(name,info[0]) for name,info in COMMANDS.items() if name not in admin]
        await self.client(SetBotCommandsRequest(BotCommandScopeDefault(),'',commands))
        try:
            peer = await self.client.get_input_entity(self.config.owner_id)
            all_commands = [BotCommand(name,info[0]) for name,info in COMMANDS.items()]
            await self.client(SetBotCommandsRequest(BotCommandScopePeer(peer),'',all_commands))
        except (ValueError, errors.PeerIdInvalidError):
            # Telegram cannot resolve an owner who has never opened this bot.
            pass

    async def reload_settings(self):
        self.settings = await self.db.bot_settings()
        for slots in (self.manager.downloads,self.manager.uploads):
            async with slots.condition:
                slots.condition.notify_all()

    async def supervise(self):
        try:
            while True:
                await self.db.lease(self.instance)
                await self.reload_settings()
                self.db_checked = time.monotonic()
                if not self.client.is_connected():
                    await asyncio.sleep(5)
                    if not self.client.is_connected():
                        raise RuntimeError('Telegram disconnected')
                for task in self.manager.active():
                    await self.manager.save(task)
                await self.status.refresh_all()
                self.offers = {k:v for k,v in self.offers.items() if v['expires'] > time.monotonic()}
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.logger.error('Main worker stopped: %s',type(error).__name__)
            self.ready = False
            self.shutdown_requested.set()

    def healthy(self):
        return bool(self.ready and self.client and self.client.is_connected()
                    and self.monitor and not self.monitor.done()
                    and time.monotonic()-self.db_checked < 30)

    async def notify(self, chat, text, **kwargs):
        try:
            return await self.client.send_message(chat,text,parse_mode='html',**kwargs)
        except Exception as error:
            self.logger.warning('Telegram notification failed: %s',type(error).__name__)

    async def completed(self, task):
        from urllib.parse import quote
        dest = task.destination
        prefix = 'datasets/' if dest['repo_type'] == 'dataset' else ''
        folder = task.options.get('path',dest['path'])
        url = f'https://huggingface.co/{prefix}{dest["namespace"]}/{dest["repo_name"]}/tree/{quote(dest["branch"],safe="")}'
        if folder: url += '/'+quote(folder,safe='/')
        done = [r for r in task.results if not r.get('skipped')]
        skipped = len(task.results)-len(done)
        text = f'✅ <b>Upload complete</b>\n\n<b>{escape(task.name)}</b>\n'
        text += f'Files: {len(done)} · Size: {size(sum(r["size"] for r in done))}\n'
        text += f'Time: {duration(time.time()-task.created)}\n'
        if skipped: text += f'Already present: {skipped}\n'
        text += f'\n<a href="{escape(url,quote=True)}">Open repository folder</a>'
        for item in task.results[:5]:
            text += f'\n<a href="{escape(item["url"],quote=True)}">{escape(item["source_name"][:120])}</a>'
        await self.notify(task.chat_id,text,link_preview=False)

    async def failed(self, task):
        await self.notify(task.chat_id,f'<b>{escape(task.phase)}</b> · {escape(task.name)}\n'
                          f'{escape(task.error[:300])}\n/retry_{task.id} · /clean_{task.id}')

    async def offer_selection(self, task):
        from bot.modules.selection import render
        text,buttons = render(task,0)
        await self.notify(task.chat_id,text,buttons=buttons)

    async def close(self):
        self.ready = False
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor,return_exceptions=True)
        for task in list(self.auxiliary): task.cancel()
        await asyncio.gather(*self.auxiliary,return_exceptions=True)
        await self.manager.close()
        if self.client: await self.client.disconnect()
        try: await self.db.release(self.instance)
        except Exception: pass
        await self.db.close()
