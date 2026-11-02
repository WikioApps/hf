"""Application logs with credential redaction and a single rotating file writer."""
import logging
from logging.handlers import RotatingFileHandler
import re
import sys

_SECRETS = set()


def register_secrets(*values):
    _SECRETS.update(str(value) for value in values if value)


def redact(value):
    text = str(value)
    for secret in sorted(_SECRETS,key=len,reverse=True):
        if len(secret) >= 6:
            text = text.replace(secret,'[REDACTED]')
        else:
            text = re.sub(r'(?<!\w)'+re.escape(secret)+r'(?!\w)','[REDACTED]',text)
    text = re.sub(r'\b\d{5,}:[A-Za-z0-9_-]{15,}','[BOT_TOKEN]',text)
    text = re.sub(r'\bhf_[A-Za-z0-9]{10,}\b','[HF_TOKEN]',text)
    text = re.sub(r'(?:https?|mongodb(?:\+srv)?)://[^\s<>\"\']+','[URL REDACTED]',text,flags=re.I)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,]+',r'\1[REDACTED]',text)
    return text


class PrivateFormatter(logging.Formatter):
    def format(self,record):
        return redact(super().format(record))


def configure(config=None):
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    formatter = PrivateFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)
    if config:
        register_secrets(config.bot_token,config.api_hash,config.database_url)
        folder = config.data/'logs'
        folder.mkdir(parents=True,exist_ok=True)
        folder.chmod(0o700)
        log = RotatingFileHandler(folder/'logs.txt',maxBytes=5*1024**2,backupCount=2,encoding='utf-8')
        log.setFormatter(formatter)
        root.addHandler(log)
        (folder/'logs.txt').chmod(0o600)
    root.setLevel(logging.INFO)
    for name in ('httpx','httpcore','telethon','urllib3'):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_snapshot(config):
    for handler in logging.getLogger().handlers:
        handler.flush()
    path = config.data/'logs/logs.txt'
    if not path.is_file():
        raise ValueError('No application log exists yet. Restart the updated bot first.')
    # Reapply redaction in case the active file came from an older process.
    return redact(path.read_text(encoding='utf-8',errors='replace')).encode('utf-8')
