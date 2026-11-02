from html import escape
import re
from telethon import Button, errors
from bot.helper.telegram_helper.bot_commands import ALIASES, help_text
from bot.modules import mirror, users, selection, speedtest
from bot.modules.logs import send_logs


class Router:
    def __init__(self,runtime):
        self.r = runtime

    async def message(self,event):
        if not event.sender_id or event.sender_id < 1: return
        try:
            sender = await event.get_sender()
            await self.r.db.user(event.sender_id,getattr(sender,'first_name','User'))
            command,body = self.command(event.raw_text)
            if command in ('start','help'):
                return await self.intro(event,command,body)
            if not await self.r.db.allowed(event.sender_id):
                if command: await event.reply('Access is restricted. Ask the bot owner to authorize your Telegram ID: '+str(event.sender_id))
                return
            if await self.r.menus.receive(event): return
            if command: return await self.dispatch(event,command,body)
            if event.message.file: await mirror.offer(self.r,event)
        except Exception as error:
            await self.error(event,error)

    def command(self,text):
        if not text.startswith('/'): return '',text
        head,*rest = text.split(maxsplit=1)
        command = head[1:].split('@')[0].lower()
        body = rest[0] if rest else ''
        if '_' in command:
            prefix,tid = command.split('_',1)
            if prefix in ('cancel','retry','clean','select'):
                command,body = prefix,tid
        return ALIASES.get(command,command),body

    async def intro(self,event,command,body):
        if command == 'help': return await event.reply(help_text(body.strip().lstrip('/')),parse_mode='html')
        if event.sender_id == self.r.config.owner_id:
            await self.r.register_commands()
        await event.reply('<b>Hugging Face Uploader</b>\nConfigure /usettings, then send a file or use /mirror URL.\n/help · /status · /history',parse_mode='html')

    async def dispatch(self,event,command,body):
        if command == 'mirror': return await mirror.mirror(self.r,event,body)
        if command == 'usettings': return await self.r.menus.show(event)
        if command == 'botsettings':
            self.owner(event)
            return await self.r.menus.show(event,'bot')
        if command == 'status': return await self.r.status.show(event,body.strip() == 'me')
        if command in ('cancel','retry','clean'): return await self.task_action(event,command,body)
        if command == 'select':
            task = await self.r.manager.find(body.strip(),event.sender_id)
            if not hasattr(task,'phase') or task.phase != 'Select': raise ValueError('No pending selection for this task.')
            return await self.r.offer_selection(task)
        if command == 'history': return await self.history(event)
        if command == 'stats': return await event.reply(self.r.status.stats()+f'\nDownloads: {self.r.manager.downloads.running} · Uploads: {self.r.manager.uploads.running}',parse_mode='html')
        if command == 'web': return await self.web(event)
        if command == 'users':
            self.owner(event)
            text,buttons = await users.page(self.r)
            return await event.reply(text,buttons=buttons,parse_mode='html')
        if command == 'logs':
            self.owner(event)
            return await send_logs(self.r,event)
        if command == 'speedtest':
            self.owner(event)
            return await speedtest.start(self.r,event)
        await event.reply('Unknown command. Use /help.')

    def owner(self,event):
        if event.sender_id != self.r.config.owner_id or not event.is_private:
            raise ValueError('Owner only. Use this command in a private chat.')

    async def task_action(self,event,command,body):
        if command not in ('cancel','retry','clean'):
            raise ValueError('Unknown task action.')
        tid = body.strip()
        if command == 'cancel' and not tid:
            if await self.r.menus.cancel(event):return
            return await event.reply('Use /cancel ID to stop a transfer. Find IDs in /status or /history.')
        if not re.fullmatch('[a-f0-9]{12}',tid): raise ValueError(f'Use /{command} ID. Find task IDs in /status or /history.')
        await getattr(self.r.manager,command)(tid,event.sender_id)
        await event.reply({'cancel':'Transfer stopped.','retry':'Retry started. Use /status.','clean':'Retained local files removed.'}[command])

    async def history(self,event):
        uid = None if event.sender_id == self.r.config.owner_id else event.sender_id
        records = await self.r.db.tasks(uid,10)
        text = '<b>Recent transfers</b>\n'
        buttons = []
        for task in records:
            text += f'\n<b>{escape(task["name"][:100])}</b> · {escape(task["phase"])}\n<code>{task["_id"]}</code>\n'
            if task.get('results'):
                text += '<a href="'+escape(task['results'][-1]['url'],quote=True)+'">Open file</a>\n'
            if task['phase'] in ('Failed','Interrupted','Cancelled'):
                buttons.append([Button.inline('Retry '+task['_id'],f'act:retry:{task["_id"]}'),Button.inline('Clean',f'act:clean:{task["_id"]}')])
        await event.reply(text if records else 'No transfers yet.',buttons=buttons,parse_mode='html',link_preview=False)

    async def web(self,event):
        if not event.is_private: raise ValueError('Use /web in a private chat to get your sign-in link.')
        base = self.r.settings['base_url']
        if not base: raise ValueError('The owner must set Website URL in /botsettings first.')
        token = await self.r.db.issue_session(event.sender_id)
        await event.reply('Your private sign-in link expires in 5 minutes. Do not share it.',
                          buttons=[[Button.url('Open portal',base+'/#login='+token)]])

    async def callback(self,event):
        try:
            if not await self.r.db.allowed(event.sender_id): raise ValueError('Access denied.')
            parts = event.data.decode().split(':')
            await event.answer()
            if parts[0] == 'cfg': await self.r.menus.callback(event,parts)
            elif parts[0] == 'st': await self.r.status.callback(event,parts)
            elif parts[0] == 'usr': await users.callback(self.r,event,parts)
            elif parts[0] == 'file': await mirror.callback(self.r,event,parts)
            elif parts[0] == 'sel': await selection.callback(self.r,event,parts)
            elif parts[0] == 'act': await self.task_action(event,parts[1],parts[2])
        except errors.MessageNotModifiedError: pass
        except Exception as error: await self.error(event,error,callback=True)

    async def error(self,event,error,callback=False):
        message = str(error) if type(error) is ValueError else 'The request failed ('+type(error).__name__+'). Check the settings and try again.'
        self.r.logger.exception('Request failed: %s',type(error).__name__)
        if callback:
            await self.r.notify(event.chat_id,escape(message[:500]))
        else:
            await event.respond(escape(message[:500]),parse_mode='html')
