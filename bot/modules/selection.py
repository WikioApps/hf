from html import escape
from bot.helper.telegram_helper.button import Button


def render(task, page):
    pages = max(1,(len(task.members)+7)//8)
    page %= pages
    text = f'<b>Select archive files</b>\n{escape(task.name)}\n{len(task.selected)} selected · Page {page+1}/{pages}'
    buttons = []
    for index in range(page*8,min(len(task.members),(page+1)*8)):
        mark = '✓ ' if index in task.selected else '○ '
        buttons.append([Button.inline(mark+task.members[index]['name'][:55],f'sel:{task.id}:toggle:{index}:{page}',style='success' if index in task.selected else None)])
    buttons += [[Button.inline('All',f'sel:{task.id}:all:0:{page}'),Button.inline('None',f'sel:{task.id}:none:0:{page}')],
                [Button.inline('‹',f'sel:{task.id}:page:0:{page-1}'),Button.inline('›',f'sel:{task.id}:page:0:{page+1}')],
                [Button.inline('Upload selected',f'sel:{task.id}:go:0:{page}'),Button.inline('Cancel',f'sel:{task.id}:cancel:0:{page}')]]
    return text,buttons


async def callback(runtime,event,parts):
    task = await runtime.manager.find(parts[1],event.sender_id)
    if not hasattr(task,'phase') or task.phase != 'Select':
        raise ValueError('This selection has expired.')
    action,index,page = parts[2],int(parts[3]),int(parts[4])
    if action == 'cancel':
        await runtime.manager.cancel(task.id,event.sender_id)
        return await event.edit('Upload cancelled. Your original message was kept.',buttons=None)
    if action == 'go':
        if not task.selected: raise ValueError('Select at least one file.')
        task.selected.sort()
        await runtime.manager.save(task)
        runtime.manager.selection_events[task.id].set()
        return await event.edit('Selected files are ready for upload.',buttons=None)
    if action == 'toggle' and 0 <= index < len(task.members):
        task.selected.remove(index) if index in task.selected else task.selected.append(index)
    elif action == 'all': task.selected = list(range(len(task.members)))
    elif action == 'none': task.selected = []
    text,buttons = render(task,page)
    await event.edit(text,buttons=buttons,parse_mode='html')
