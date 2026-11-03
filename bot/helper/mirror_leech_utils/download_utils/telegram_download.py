import logging
from bot.helper.ext_utils.files_utils import check_space, filename

LOGGER = logging.getLogger(__name__)


async def download(task, client, settings):
    source = task.source
    message = await client.get_messages(source['chat'],ids=source['message'])
    if not message or not message.file:
        raise ValueError('The Telegram source is unavailable. Forward the file again.')
    total = int(message.file.size or 0)
    limit = settings.get('byte_limit',settings['max_file_gb']*1024**3)
    if total > limit:
        raise ValueError('The Telegram file exceeds the configured size limit.')
    if not task.options.get('name'):
        task.name = filename(message.file.name or f'file_{message.id}{message.file.ext or ".bin"}')
    task.engine = 'Telegram MTProto'
    task.stage('Download',total)

    def progress(done, expected):
        if done > limit:
            raise ValueError('The Telegram file exceeds the configured size limit.')
        check_space(task.directory)
        task.done,task.total = done,int(expected or total)

    path = task.directory/'source'
    legacy = task.directory/('source'+(message.file.ext or ''))
    if total and not path.exists() and legacy.is_file() and legacy.stat().st_size == total:
        legacy.replace(path)
        task.done = total
        LOGGER.info('Recovered completed legacy Telegram download task=%s bytes=%s',task.id,total)
        return path
    check_space(task.directory,total)
    # Passing a stream prevents Telethon appending .mkv/.zip to our internal path.
    with path.open('wb') as output:
        await client.download_media(message,file=output,progress_callback=progress)
    received = path.stat().st_size
    if (total and received != total) or received > limit:
        LOGGER.error('Telegram size mismatch task=%s expected=%s received=%s',task.id,total,received)
        raise ValueError(f'Incomplete Telegram download: received {received} of {total} bytes. Retry the task.')
    if not total and not received:
        raise ValueError('Telegram returned no file data. Forward the file again.')
    task.done = received
    LOGGER.info('Telegram download complete task=%s bytes=%s file=%s',task.id,received,task.name)
    return path
