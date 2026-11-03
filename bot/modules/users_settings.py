import time
from html import escape
from telethon import errors
from bot.helper.telegram_helper.button import Button
from bot.core.settings import BOT_DEFAULTS, CHOICES, LABELS, validate
from bot.helper.ext_utils.process_utils import run_worker
from bot.helper.ext_utils.log_utils import register_secrets

USER_KEYS = ['token','namespace','repo_name','repo_type','branch','path','private','collision','processing']


class SettingsMenu:
    def __init__(self,runtime):
        self.runtime = runtime
        self.prompts = {}

    async def values(self,uid,scope):
        if scope not in ('user','bot'):
            raise ValueError('Unknown settings menu.')
        if scope == 'bot':
            if uid != self.runtime.config.owner_id:
                raise ValueError('Only the owner can change bot settings.')
            return await self.runtime.db.bot_settings()
        return await self.runtime.db.user(uid)

    async def anchor(self,uid,scope):
        user = await self.runtime.db.user(uid)
        return user.get('settings_menus',{}).get(scope)

    async def remember(self,uid,scope,chat,message):
        await self.runtime.db.update_user(uid,'settings_menus.'+scope,{'chat':chat,'message':message})

    async def delete(self,chat,message):
        try:
            await self.runtime.client.delete_messages(chat,message if isinstance(message,list) else [message])
        except errors.RPCError as error:
            self.runtime.logger.warning('Settings message deletion failed: %s',type(error).__name__)

    async def render(self,uid,scope,text,buttons,chat):
        anchor = await self.anchor(uid,scope)
        if anchor:
            try:
                return await self.runtime.client.edit_message(anchor['chat'],anchor['message'],text,buttons=buttons,parse_mode='html')
            except errors.MessageNotModifiedError:
                return
            except (errors.MessageIdInvalidError,errors.MessageAuthorRequiredError,errors.MessageEditTimeExpiredError):
                await self.delete(anchor['chat'],anchor['message'])
        message = await self.runtime.client.send_message(chat,text,buttons=buttons,parse_mode='html')
        await self.remember(uid,scope,chat,message.id)

    async def refresh(self,uid,scope,chat):
        text,buttons = await self.menu(uid,scope)
        await self.render(uid,scope,text,buttons,chat)

    async def menu(self,uid,scope):
        values = await self.values(uid,scope)
        keys = list(BOT_DEFAULTS) if scope == 'bot' else USER_KEYS
        title = 'Bot settings' if scope == 'bot' else 'Your Hugging Face settings'
        text = f'<b>{title}</b>\nChanges are saved to MongoDB.'
        buttons = []
        for key in keys:
            value = ('Configured' if values[key] else 'Not set') if key == 'token' else str(values[key] if values[key] != '' else 'Not set')
            if isinstance(values[key],bool):value = 'On' if values[key] else 'Off'
            buttons.append([Button.inline(f'{LABELS[key]}: {value[:38]}',f'cfg:{uid}:{scope}:{key}')])
        buttons.append([Button.inline('Close',f'cfg:{uid}:{scope}:close')])
        return text,buttons

    async def cleanup_legacy(self,uid,chat,before):
        user = await self.runtime.db.user(uid)
        if user.get('settings_messages_migrated'):
            return
        titles = ('Your Hugging Face settings','Bot settings')
        try:
            # Bots cannot request chat history; look up only recent explicit IDs.
            ids = list(range(max(1,before-100),before))
            messages = await self.runtime.client.get_messages(chat,ids=ids) if ids else []
            stale = []
            for message in messages:
                if not message or message.chat_id != chat or message.sender_id != int(self.runtime.config.bot_id):
                    continue
                text = message.raw_text or ''
                menu = text.startswith(titles) and 'Changes are saved to MongoDB.' in text
                prompt = any(text.startswith(label+'\n') for label in LABELS.values()) and 'Send the new value' in text
                if menu or prompt:
                    stale.append(message.id)
            if stale:
                await self.delete(chat,stale)
            await self.runtime.db.update_user(uid,'settings_messages_migrated',True)
        except errors.RPCError as error:
            self.runtime.logger.warning('Legacy settings cleanup unavailable: %s',type(error).__name__)
            await self.runtime.db.update_user(uid,'settings_messages_migrated',True)

    async def show(self,event,scope='user'):
        if not event.is_private:
            return await event.reply('Open this menu in a private chat with the bot.')
        await self.values(event.sender_id,scope)
        self.prompts.pop(event.sender_id,None)
        await self.cleanup_legacy(event.sender_id,event.chat_id,event.id)
        anchor = await self.anchor(event.sender_id,scope)
        if anchor:
            await self.delete(anchor['chat'],anchor['message'])
        await self.runtime.db.update_user(event.sender_id,'settings_menus.'+scope,None)
        await self.refresh(event.sender_id,scope,event.chat_id)
        await self.delete(event.chat_id,event.id)

    async def callback(self,event,parts):
        uid,scope,key = int(parts[1]),parts[2],parts[3]
        if uid != event.sender_id or not event.is_private:
            raise ValueError('This private menu belongs to another user.')
        values = await self.values(uid,scope)
        anchor = await self.anchor(uid,scope)
        if anchor and anchor['message'] != event.message_id:
            raise ValueError('This menu is outdated. Open /usettings or /botsettings again.')
        await self.remember(uid,scope,event.chat_id,event.message_id)
        self.prompts.pop(uid,None)
        if key == 'close':
            await self.delete(event.chat_id,event.message_id)
            return await self.runtime.db.update_user(uid,'settings_menus.'+scope,None)
        if key == 'back':return await self.refresh(uid,scope,event.chat_id)
        if key not in (BOT_DEFAULTS if scope == 'bot' else USER_KEYS):
            raise ValueError('Unknown setting.')
        if len(parts)>4:
            return await self.choose(event,uid,scope,key,parts[4],values)
        if key in CHOICES:
            buttons = [[Button.inline(str(value),f'cfg:{uid}:{scope}:{key}:{i}',style='success' if value == values[key] else 'primary')] for i,value in enumerate(CHOICES[key])]
        elif key == 'namespace' and values.get('namespaces'):
            buttons = [[Button.inline(value,f'cfg:{uid}:{scope}:{key}:{i}')] for i,value in enumerate(values['namespaces'][:30])]
        elif key == 'branch':buttons = await self.branches(uid,scope,values)
        else:return await self.prompt(event,uid,scope,key)
        buttons += [[Button.inline('Back',f'cfg:{uid}:{scope}:back')]]
        await event.edit('<b>'+LABELS[key]+'</b>\nChoose a value.',buttons=buttons,parse_mode='html')

    async def choose(self,event,uid,scope,key,value,values):
        if value == 'custom':return await self.prompt(event,uid,scope,key)
        if key == 'namespace':value = values['namespaces'][int(value)]
        elif key == 'branch':value = values.get('branches',['main'])[int(value)]
        else:value = CHOICES[key][int(value)]
        await self.save(uid,scope,key,validate(key,value))
        await self.refresh(uid,scope,event.chat_id)

    async def prompt(self,event,uid,scope,key):
        self.prompts[uid] = {'scope':scope,'key':key,'expires':time.monotonic()+300}
        hint = ' Send - to clear it.' if key in ('token','path','base_url') else ''
        await event.edit('<b>'+LABELS[key]+'</b>\nSend the new value within 5 minutes.'+hint+'\n/cancel to leave it unchanged.',
            buttons=[[Button.inline('Back',f'cfg:{uid}:{scope}:back')]],parse_mode='html')

    async def receive(self,event):
        prompt = self.prompts.get(event.sender_id)
        if not prompt or not event.is_private or event.raw_text.startswith('/'):
            return False
        if prompt['expires']<time.monotonic():
            self.prompts.pop(event.sender_id,None)
            await self.refresh(event.sender_id,prompt['scope'],event.chat_id)
            return True
        key,value = prompt['key'],event.raw_text.strip()
        if key == 'token':
            register_secrets(value)
            await self.delete(event.chat_id,event.id)
        try:
            value = '' if value == '-' and key in ('token','path','base_url') else value
            value = validate(key,value)
            if key == 'token' and value:await self.verify_token(event.sender_id,value)
            if self.prompts.get(event.sender_id) is not prompt:
                return True
            await self.save(event.sender_id,prompt['scope'],key,value)
        except Exception as error:
            self.runtime.logger.exception('Settings update failed field=%s user=%s',key,event.sender_id)
            message = str(error) if type(error) is ValueError else 'Could not save this setting ('+type(error).__name__+'). Try again.'
            await self.render(event.sender_id,prompt['scope'],'<b>'+LABELS[key]+'</b>\n'+escape(message)+'\nSend another value or /cancel.',
                [[Button.inline('Back',f'cfg:{event.sender_id}:{prompt["scope"]}:back')]],event.chat_id)
            return True
        self.prompts.pop(event.sender_id,None)
        if key != 'token':await self.delete(event.chat_id,event.id)
        await self.refresh(event.sender_id,prompt['scope'],event.chat_id)
        return True

    async def verify_token(self,uid,value):
        info = await self.probe('inspect_hf',{'token':value})
        await self.runtime.db.update_user(uid,'namespaces',[info['name'],*info['organizations']])
        current = await self.runtime.db.user(uid)
        if not current['namespace']:
            await self.runtime.db.update_user(uid,'namespace',info['name'])

    async def cancel(self,event):
        prompt = self.prompts.pop(event.sender_id,None)
        if not prompt:return False
        await self.delete(event.chat_id,event.id)
        await self.refresh(event.sender_id,prompt['scope'],event.chat_id)
        return True

    async def save(self,uid,scope,key,value):
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
