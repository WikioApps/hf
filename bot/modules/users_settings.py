import time
from html import escape
from types import SimpleNamespace
from telethon import Button
from bot.core.settings import USER_DEFAULTS, BOT_DEFAULTS, CHOICES, LABELS, validate
from bot.helper.ext_utils.process_utils import run_worker

USER_KEYS = ['token','namespace','repo_name','repo_type','branch','path','private','collision','processing']


class SettingsMenu:
    def __init__(self, runtime):
        self.runtime = runtime
        self.prompts = {}

    async def values(self, uid, scope):
        if scope not in ('user', 'bot'):
            raise ValueError('Unknown settings menu.')
        if scope == 'bot':
            if uid != self.runtime.config.owner_id:
                raise ValueError('Only the owner can change bot settings.')
            return await self.runtime.db.bot_settings()
        return await self.runtime.db.user(uid)

    async def menu(self, uid, scope):
        values = await self.values(uid,scope)
        keys = list(BOT_DEFAULTS) if scope == 'bot' else USER_KEYS
        title = 'Bot settings' if scope == 'bot' else 'Your Hugging Face settings'
        text = f'<b>{title}</b>\nChanges are saved to MongoDB.\n'
        buttons = []
        for key in keys:
            value = ('Configured' if values[key] else 'Not set') if key == 'token' else str(values[key] if values[key] != '' else 'Not set')
            if isinstance(values[key],bool):
                value = 'On' if values[key] else 'Off'
            buttons.append([Button.inline(f'{LABELS[key]}: {value[:38]}',f'cfg:{uid}:{scope}:{key}')])
        buttons.append([Button.inline('Close',f'cfg:{uid}:{scope}:close')])
        return text,buttons

    async def show(self, event, scope='user'):
        if not event.is_private:
            return await event.reply('Open this menu in a private chat with the bot.')
        text,buttons = await self.menu(event.sender_id,scope)
        await event.reply(text,buttons=buttons,parse_mode='html')

    async def callback(self, event, parts):
        uid,scope,key = int(parts[1]),parts[2],parts[3]
        if uid != event.sender_id or not event.is_private:
            raise ValueError('This private menu belongs to another user.')
        values = await self.values(uid,scope)
        allowed = BOT_DEFAULTS if scope == 'bot' else USER_KEYS
        if key == 'close':
            self.prompts.pop(uid,None)
            return await event.delete()
        if key == 'back':
            text,buttons = await self.menu(uid,scope)
            return await event.edit(text,buttons=buttons,parse_mode='html')
        if key not in allowed:
            raise ValueError('Unknown setting.')
        if len(parts) > 4:
            return await self.choose(event,uid,scope,key,parts[4],values)
        if key in CHOICES:
            buttons = [[Button.inline(str(value),f'cfg:{uid}:{scope}:{key}:{i}')]
                       for i,value in enumerate(CHOICES[key])]
        elif key == 'namespace' and values.get('namespaces'):
            buttons = [[Button.inline(value,f'cfg:{uid}:{scope}:{key}:{i}')]
                       for i,value in enumerate(values['namespaces'][:30])]
        elif key == 'branch':
            buttons = await self.branches(uid,scope,values)
        else:
            return await self.prompt(event,uid,scope,key)
        buttons += [[Button.inline('Back',f'cfg:{uid}:{scope}:back')]]
        await event.edit('<b>'+LABELS[key]+'</b>\nChoose a value.',buttons=buttons,parse_mode='html')

    async def choose(self, event, uid, scope, key, value, values):
        if value == 'custom':
            return await self.prompt(event,uid,scope,key)
        if key == 'namespace':
            value = values['namespaces'][int(value)]
        elif key == 'branch':
            value = values.get('branches',['main'])[int(value)]
        else:
            value = CHOICES[key][int(value)]
        await self.save(uid,scope,key,validate(key,value))
        text,buttons = await self.menu(uid,scope)
        await event.edit(text,buttons=buttons,parse_mode='html')

    async def prompt(self, event, uid, scope, key):
        self.prompts[uid] = {'scope':scope,'key':key,'expires':time.monotonic()+300}
        hint = ' Send - to clear it.' if key in ('token','path','base_url') else ''
        await event.edit('<b>'+LABELS[key]+'</b>\nSend the new value in this chat within 5 minutes.'+hint+'\n/cancel to leave it unchanged.',buttons=None,parse_mode='html')

    async def receive(self, event):
        prompt = self.prompts.get(event.sender_id)
        if not prompt or not event.is_private:
            return False
        if prompt['expires'] < time.monotonic():
            self.prompts.pop(event.sender_id,None)
            return False
        if event.raw_text.startswith('/'):
            return False
        key,value = prompt['key'],event.raw_text.strip()
        if key == 'token':
            try: await event.delete()
            except Exception: pass
        value = '' if value == '-' and key in ('token','path','base_url') else value
        value = validate(key,value)
        if key == 'token' and value:
            info = await self.probe('inspect_hf',{'token':value})
            await self.runtime.db.update_user(event.sender_id,'namespaces',[info['name'],*info['organizations']])
            current = await self.runtime.db.user(event.sender_id)
            if not current['namespace']:
                await self.runtime.db.update_user(event.sender_id,'namespace',info['name'])
        await self.save(event.sender_id,prompt['scope'],key,value)
        self.prompts.pop(event.sender_id,None)
        text,buttons = await self.menu(event.sender_id,prompt['scope'])
        await event.respond(text,buttons=buttons,parse_mode='html')
        return True

    async def save(self, uid, scope, key, value):
        if scope == 'bot':
            await self.runtime.db.update_bot(key,value)
            await self.runtime.reload_settings()
        else:
            await self.runtime.db.update_user(uid,key,value)

    async def probe(self, mode, payload):
        from bot.helper.mirror_leech_utils.status_utils.task_status import TaskStatus
        task = TaskStatus(0,0,0,{}, {}, {},self.runtime.config.data)
        return await run_worker(task,mode,[],secret=payload,timeout=45)

    async def branches(self, uid, scope, values):
        branches = ['main']
        if values['token'] and values['namespace'] and values['repo_name']:
            try:
                branches = await self.probe('branches',{'token':values['token'],
                    'repo':values['namespace']+'/'+values['repo_name'],'repo_type':values['repo_type']})
            except Exception:
                pass
        branches = list(dict.fromkeys([values['branch'],*branches]))[:30]
        await self.runtime.db.update_user(uid,'branches',branches)
        buttons = [[Button.inline(branch,f'cfg:{uid}:{scope}:branch:{i}')] for i,branch in enumerate(branches)]
        return buttons+[[Button.inline('New branch',f'cfg:{uid}:{scope}:branch:custom')]]
