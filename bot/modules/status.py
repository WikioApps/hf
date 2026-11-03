import asyncio
from html import escape
import shutil
import time
import psutil
from telethon import Button, errors
from bot.helper.ext_utils.format_utils import size, duration, progress


def task_text(task):
    text = f'<b>{escape(task.name[:100])}</b>\n'
    text += f'┠ <b>Task</b> → {escape(task.operation)}\n'
    groups = task.snapshot()
    for group in groups[:3]:
        phase,done,total,speed = (group[k] for k in ('phase','done','total','speed'))
        count = ' ×'+str(group['count']) if group['count']>1 else ''
        text += f'┠ <b>{escape(phase)}{count}</b>'
        if phase in ('Download','Upload','Extract'):
            eta=max(0,total-done)/speed if speed and total else None
            text += f' {progress(done,total)}\n┠ {size(done)} / {size(total) if total else "?"} · {size(speed)}/s · ETA {duration(eta)}'
        text += '\n'
    waiting = next((c.wait_reason for c in task.lanes.values() if c.wait_reason),task.wait_reason)
    if waiting:text += '┠ '+escape(waiting)+'\n'
    if task.lanes:
        names = [c.name for c in task.lanes.values() if c.phase not in ('Pipeline','Complete')]
        text += '┠ '+escape(' · '.join(names[:2])[:130])+'\n'
    text += f'┠ <b>Uploaded</b> → {len(task.results)} files · <b>Elapsed</b> → {duration(time.time()-task.created)}\n'
    text += f'┠ <b>User</b> → <a href="tg://user?id={task.user_id}">{task.user_id}</a>\n'
    return text+f'┖ /cancel_{task.id}\n'


class StatusViews:
    def __init__(self, runtime):
        self.runtime = runtime
        self.views = {}
        self.lock = asyncio.Lock()

    def stats(self):
        disk = shutil.disk_usage(self.runtime.config.data)
        return f'⌬ <b>Bot Stats</b>\n┟ <b>CPU</b> → {psutil.cpu_percent()}% | <b>Free</b> → {size(disk.free)}\n┖ <b>RAM</b> → {psutil.virtual_memory().percent}% | <b>Uptime</b> → {duration(time.time()-self.runtime.started)}'

    def visible(self, view):
        uid = view['user'] if view['me'] or view['user'] != self.runtime.config.owner_id else None
        return self.runtime.manager.active(uid,view['chat'])

    def render(self, view):
        tasks = self.visible(view)
        if not tasks:
            return None,None
        pages = max(1,(len(tasks)+2)//3)
        view['page'] %= pages
        text = '\n'.join(task_text(task) for task in tasks[view['page']*3:][:3])
        text += f'\n<b>Tasks</b> → {len(tasks)} | <b>Page</b> → {view["page"]+1}/{pages}\n\n'+self.stats()
        prefix = f'st:{view["user"]}:'
        buttons = [Button.inline('Refresh',prefix+'refresh')]
        if pages > 1:
            buttons = [Button.inline('‹',prefix+'previous'),*buttons,Button.inline('›',prefix+'next')]
        return text,[buttons]

    async def show(self, event, me=False):
        key = (event.chat_id,event.sender_id)
        async with self.lock:
            await self.remove(key)
            view = {'user':event.sender_id,'chat':event.chat_id,'page':0,'me':me,'blocked_until':0}
            text,buttons = self.render(view)
            if text is None:
                return await event.reply('No active transfers.')
            view['message'] = await event.reply(text,buttons=buttons,parse_mode='html')
            self.views[key] = view

    async def remove(self, key):
        view = self.views.get(key)
        if not view:
            return
        try:
            await view['message'].delete()
            self.views.pop(key,None)
        except errors.FloodWaitError as error:
            view['blocked_until'] = time.monotonic()+error.seconds+1
        except errors.RPCError:
            # If Telegram refuses deletion, clear live controls and then forget it.
            try:
                await view['message'].edit('All transfers finished.',buttons=None)
                self.views.pop(key,None)
            except errors.RPCError:
                pass

    async def refresh_all(self):
        async with self.lock:
            for key,view in list(self.views.items()):
                if time.monotonic() < view['blocked_until']:
                    continue
                text,buttons = self.render(view)
                if text is None:
                    await self.remove(key)
                    continue
                try:
                    await view['message'].edit(text,buttons=buttons,parse_mode='html')
                except errors.MessageNotModifiedError:
                    pass
                except errors.FloodWaitError as error:
                    view['blocked_until'] = time.monotonic()+error.seconds+1
                except errors.RPCError:
                    self.views.pop(key,None)

    async def callback(self, event, parts):
        if int(parts[1]) != event.sender_id:
            return await event.answer('Open your own /status view.',alert=True)
        view = self.views.get((event.chat_id,event.sender_id))
        if view:
            view['page'] += {'next':1,'previous':-1}.get(parts[2],0)
        await event.answer()
        await self.refresh_all()
