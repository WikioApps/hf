from bot.helper.ext_utils.files_utils import check_space, filename

async def download(task, client, settings):
    source = task.source
    message = await client.get_messages(source['chat'], ids=source['message'])
    if not message or not message.file:
        raise ValueError('The Telegram source is unavailable. Forward the file again.')
    total = message.file.size
    if total > settings['max_file_gb']*1024**3:
        raise ValueError('The Telegram file exceeds the configured size limit.')
    check_space(task.directory,total)
    if not task.options.get('name'):
        task.name = filename(message.file.name or f'file_{message.id}{message.file.ext or ".bin"}')
    task.engine = 'Telegram MTProto'
    task.stage('Download',total)
    def progress(done, expected):
        check_space(task.directory)
        task.done,task.total = done,expected
    path = task.directory/'source'
    await client.download_media(message,file=str(path),progress_callback=progress)
    if not path.exists() or path.stat().st_size != total:
        raise ValueError('Incomplete Telegram download.')
    return path
