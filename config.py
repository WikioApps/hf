"""Validated environment configuration; no credentials are written to logs."""
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent


def safe_path(value: str, *, empty=False) -> str:
    value = value.strip().replace('\\', '/')
    if not value and empty:
        return ''
    if not value or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Repository paths must be relative and contain no empty, . or .. segments')
    if any(ord(c) < 32 for c in value) or ':' in value:
        raise ValueError('Invalid path characters')
    return str(PurePosixPath(value))


def filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip('. ')[:180]
    if not value:
        raise ValueError('Empty filename')
    return value


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    hf_token: str
    repo_id: str
    allowed_users: frozenset
    repo_type: str = 'model'
    branch: str = 'main'
    prefix: str = ''
    data: Path = ROOT / 'data'
    port: int = 8000
    downloads: int = 2
    uploads: int = 1
    max_tasks: int = 20
    interval: int = 5
    max_bytes: int = 20 * 1024**3
    reserve: int = 512 * 1024**2
    max_extract: int = 50 * 1024**3
    max_members: int = 10000
    private_repo: bool = True
    user_session: str = ''

    @classmethod
    def load(cls):
        if not (3, 11) <= sys.version_info[:2] < (3, 14):
            raise ValueError('Python 3.11–3.13 is required; select Python 3.12 on InstaPods')
        # Explicitly load the platform .env, preserving already-exported values.
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env', override=False)
        needed = ['API_ID', 'API_HASH', 'BOT_TOKEN', 'OWNER_ID', 'HF_TOKEN', 'REPO_ID']
        missing = [n for n in needed if not os.getenv(n, '').strip()]
        if missing:
            raise ValueError('Missing required environment variables: ' + ', '.join(missing))
        def num(n, default=None, low=1, high=2**63):
            try:
                v = int(os.getenv(n, str(default)))
            except ValueError:
                raise ValueError(f'{n} must be an integer') from None
            if not low <= v <= high:
                raise ValueError(f'{n} must be between {low} and {high}')
            return v
        api_id, owner = num('API_ID'), num('OWNER_ID')
        if not re.fullmatch(r'[a-fA-F0-9]{32}', os.environ['API_HASH']):
            raise ValueError('API_HASH must contain 32 hexadecimal characters')
        if not re.fullmatch(r'\d+:[\w-]+', os.environ['BOT_TOKEN']):
            raise ValueError('BOT_TOKEN must be a BotFather token')
        repo = os.environ['REPO_ID'].strip()
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise ValueError('REPO_ID must be namespace/repository')
        kind = os.getenv('REPO_TYPE', 'model')
        if kind not in ('model', 'dataset'):
            raise ValueError('REPO_TYPE must be model or dataset')
        try:
            users = frozenset({owner} | {int(x) for x in os.getenv('ALLOWED_USERS', '').replace(',', ' ').split()})
        except ValueError:
            raise ValueError('ALLOWED_USERS must contain numeric Telegram user IDs') from None
        private = os.getenv('HF_PRIVATE', 'true').lower()
        if private not in ('true', 'false'):
            raise ValueError('HF_PRIVATE must be true or false')
        return cls(api_id, os.environ['API_HASH'], os.environ['BOT_TOKEN'], owner,
                   os.environ['HF_TOKEN'], repo, users, kind,
                   safe_path(os.getenv('BRANCH', 'main')), safe_path(os.getenv('PATH_IN_REPO', ''), empty=True),
                   Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).expanduser().resolve(),
                   num('PORT', 8000, high=65535), num('DOWNLOAD_WORKERS', 2, high=8),
                   num('UPLOAD_WORKERS', 1, high=4), num('MAX_TASKS', 20, high=100),
                   num('STATUS_UPDATE_INTERVAL', 5, low=3, high=60),
                   num('MAX_FILE_GB', 20) * 1024**3, num('MIN_FREE_MB', 512) * 1024**2,
                   num('MAX_EXTRACT_GB', 50) * 1024**3, num('MAX_ARCHIVE_MEMBERS', 10000),
                   private == 'true', os.getenv('USER_SESSION_STRING', ''))
