"""Only bootstrap credentials belong in the environment."""
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    database_url: str
    port: int
    data: Path

    @property
    def bot_id(self):
        return self.bot_token.split(':', 1)[0]

    @classmethod
    def load(cls):
        if not (3, 11) <= sys.version_info[:2] < (3, 14):
            raise ValueError('Select Python 3.12; supported versions are 3.11–3.13.')
        load_dotenv(ROOT / '.env', override=False)
        names = ('API_ID', 'API_HASH', 'BOT_TOKEN', 'OWNER_ID', 'DATABASE_URL')
        missing = [n for n in names if not os.getenv(n, '').strip()]
        if missing:
            raise ValueError('Missing environment variables: ' + ', '.join(missing))
        try:
            api_id, owner, port = (int(os.getenv(n, d)) for n, d in
                                  [('API_ID', ''), ('OWNER_ID', ''), ('PORT', '8000')])
        except ValueError:
            raise ValueError('API_ID, OWNER_ID and PORT must be integers.') from None
        if min(api_id, owner) <= 0 or not 1 <= port <= 65535:
            raise ValueError('Invalid ID or port.')
        api_hash, token, url = (os.environ[n].strip() for n in ('API_HASH', 'BOT_TOKEN', 'DATABASE_URL'))
        if not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
            raise ValueError('API_HASH must contain 32 hexadecimal characters.')
        if not re.fullmatch(r'\d+:[\w-]+', token):
            raise ValueError('Invalid BOT_TOKEN.')
        if not url.startswith(('mongodb://', 'mongodb+srv://')):
            raise ValueError('DATABASE_URL must be a MongoDB connection URI.')
        data = ROOT.parent / 'hf-data'
        return cls(api_id, api_hash, token, owner, url, port, data)
