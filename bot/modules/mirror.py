import re
import shlex
import time
import uuid
from html import escape
from bot.helper.telegram_helper.button import Button
from bot.helper.ext_utils.files_utils import filename, safe_path


def parse(line):
    words = shlex.split(line)
    url,options = '',{}
    index = 0
    values = {'-n':'name','-p':'path','-pw':'password'}
    modes = {'-e':'extract','-unzip':'extract','-z':'zip','-7z':'7z'}
    while index < len(words):
        word = words[index]
        if word in values:
            index += 1
            if index >= len(words): raise ValueError(f'{word} requires a value.')
            options[values[word]] = words[index]
        elif word in modes: options['processing'] = modes[word]
        elif word == '-s': options['select'] = True
        elif not url and word.startswith(('https://','http://')): url = word
        else: raise ValueError('Unknown option. See /help mirror. Quote filenames that contain spaces.')
        index += 1
    if 'name' in options: options['name'] = filename(options['name'])
    if 'path' in options: options['path'] = safe_path(options['path'],empty=True)
    if options.get('select') and options.get('processing') != 'extract':
        raise ValueError('Use -e -s together to select archive files.')
    return url,options


def media_source(message):
    if not message or not message.file:
        raise ValueError('Reply to a downloadable file or provide an HTTP link.')
    return {'kind':'telegram','chat':message.chat_id,'message':message.id,
            'name':filename(message.file.name or f'file_{message.id}{message.file.ext or ".bin"}')}


async def source_for(runtime,url,reply):
    if not url: return media_source(reply)
    match = re.fullmatch(r'https?://(?:t\.me|telegram\.me)/(c/\d+|[A-Za-z0-9_]+)/(\d+)(?:\?.*)?',url)
    if match:
        chat = int('-100'+match[1][2:]) if match[1].startswith('c/') else match[1]
        message = await runtime.client.get_messages(chat,ids=int(match[2]))
        return media_source(message)
    return {'kind':'url','url':url}


async def mirror(runtime,event,body):
    lines = body.splitlines() or ['']
    reply = await event.get_reply_message()
    for line in lines:
        url,options = parse(line)
        source = await source_for(runtime,url,reply)
        await runtime.manager.add(event.sender_id,event.chat_id,event.id,source,options)
    await runtime.status.show(event,me=True)


async def offer(runtime,event):
    key = uuid.uuid4().hex[:12]
    runtime.offers[key] = {'user':event.sender_id,'chat':event.chat_id,'message':event.id,
                          'source':media_source(event.message),'expires':time.monotonic()+900}
    buttons = [[Button.inline('Upload',f'file:{key}:upload'),Button.inline('Cancel upload',f'file:{key}:cancel')],
               [Button.inline('For Other Purpose Forwarded',f'file:{key}:keep')]]
    await event.reply('<b>'+escape(runtime.offers[key]['source']['name'])+'</b>\nUpload this file to Hugging Face?',
                      buttons=buttons,parse_mode='html')


async def callback(runtime,event,parts):
    value = runtime.offers.get(parts[1])
    if not value or value['expires'] < time.monotonic():
        raise ValueError('This offer expired. Reply to the original file with /mirror.')
    if value['user'] != event.sender_id: raise ValueError('This file offer belongs to another user.')
    # Consume before starting; a double click cannot create a second task.
    runtime.offers.pop(parts[1],None)
    if parts[2] == 'upload':
        try:
            await runtime.manager.add(event.sender_id,value['chat'],value['message'],value['source'],{})
        except Exception:
            runtime.offers[parts[1]] = value
            raise
        await event.edit('Upload accepted. /status shows transfer progress.',buttons=None)
        await runtime.status.show(event,me=True)
    else:
        await event.edit('Kept for another purpose. No transfer started.' if parts[2] == 'keep'
                         else 'Upload cancelled. Your original message was kept.',buttons=None)
