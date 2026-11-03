from html import escape
from bot.helper.telegram_helper.button import Button

async def page(runtime, offset=0):
    users = await runtime.db.users(offset)
    text = '<b>User access</b>\nPublic mode: '+('On' if runtime.settings['public_mode'] else 'Off')+'\n'
    buttons = []
    for user in users:
        uid = user['user_id']
        label = escape(user.get('name') or str(uid))
        state = 'Blocked' if user['blocked'] else 'Authorized' if user['authorized'] else 'Public policy'
        text += f'\n{label} · <code>{uid}</code> · {state}'
        if uid != runtime.config.owner_id:
            buttons.append([Button.inline(f'Authorize {uid}',f'usr:{uid}:allow:{offset}'),
                            Button.inline('Block',f'usr:{uid}:block:{offset}'),
                            Button.inline('Reset',f'usr:{uid}:reset:{offset}')])
    buttons.append([Button.inline('‹',f'usr:0:page:{max(0,offset-8)}'),Button.inline('›',f'usr:0:page:{offset+8}')])
    return text,buttons

async def callback(runtime, event, parts):
    if event.sender_id != runtime.config.owner_id or not event.is_private:
        raise ValueError('Owner only. Open /users privately.')
    uid,action,offset = int(parts[1]),parts[2],int(parts[3])
    if action != 'page' and uid != runtime.config.owner_id:
        await runtime.db.user(uid)
        await runtime.db.update_user(uid,'authorized',action == 'allow')
        await runtime.db.update_user(uid,'blocked',action == 'block')
    text,buttons = await page(runtime,offset)
    await event.edit(text,buttons=buttons,parse_mode='html')
