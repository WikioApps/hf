"""Native Telegram button styles shared by all bot menus."""
from telethon import Button as TelegramButton
from telethon.tl.types import KeyboardButtonStyle


def action_style(data):
    parts = (data.decode() if isinstance(data,bytes) else str(data or '')).split(':')
    if any(value in parts for value in ('cancel','clean','clear','block')):
        return 'danger'
    if any(value in parts for value in ('upload','go','retry','allow')):
        return 'success'
    if any(value in parts for value in ('back','close','keep','previous','next','page','reset')):
        return None
    return 'primary'


def styled(button, style):
    if style:
        if style not in ('primary','success','danger'):
            raise ValueError('Invalid Telegram button style.')
        button.style = KeyboardButtonStyle(**{'bg_'+style:True})
    else:
        button.style = None
    return button


class Button:
    @staticmethod
    def inline(text, data=None, *, style='auto'):
        return styled(TelegramButton.inline(text,data),
                      action_style(data) if style == 'auto' else style)

    @staticmethod
    def url(text, url=None, *, style='primary'):
        return styled(TelegramButton.url(text,url),style)
