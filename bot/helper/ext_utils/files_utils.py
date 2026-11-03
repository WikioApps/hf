import re
import shutil
from pathlib import PurePosixPath


def safe_path(value, empty=False):
    value = value.strip().replace('\\', '/')
    if not value and empty:
        return ''
    if not value or value.startswith('/') or any(p in ('', '.', '..', '.git') for p in value.split('/')):
        raise ValueError('Use a relative folder path without . or .. segments.')
    if len(value) > 1024:
        raise ValueError('Path exceeds 1024 characters.')
    if any(ord(c) < 32 for c in value) or ':' in value:
        raise ValueError('Invalid path characters.')
    return str(PurePosixPath(value))


def filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip('. ')
    if len(value.encode('utf-8'))>180:
        suffix=PurePosixPath(value).suffix
        if len(suffix.encode('utf-8'))>30:suffix=''
        stem=value[:-len(suffix)] if suffix else value
        value=stem.encode('utf-8')[:180-len(suffix.encode('utf-8'))].decode('utf-8',errors='ignore')+suffix
    if not value:
        raise ValueError('Empty filename.')
    return value


DISK_RESERVE = 512 * 1024**2


def check_space(directory, required=0, reserve=None):
    floor = DISK_RESERVE if reserve is None else reserve
    if shutil.disk_usage(directory).free < floor + max(0, required):
        raise ValueError('The storage reserve would be exceeded. Wait for active uploads or increase host storage.')
