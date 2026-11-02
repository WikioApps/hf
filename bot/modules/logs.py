import asyncio
from io import BytesIO
from bot.helper.ext_utils.log_utils import log_snapshot


async def send_logs(runtime,event):
    if event.sender_id != runtime.config.owner_id or not event.is_private:
        raise ValueError('Owner only. Use /logs in a private chat.')
    runtime.logger.info('Owner requested the application log')
    contents = await asyncio.to_thread(log_snapshot,runtime.config)
    file = BytesIO(contents or b'No log entries yet.\n')
    file.name = 'logs.txt'
    await runtime.client.send_file(event.chat_id,file,force_document=True,
        caption='Current bot log: transfers, worker errors and service events. Credentials and URLs are redacted.')
