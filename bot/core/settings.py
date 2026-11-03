"""Shared validation for Telegram and web settings."""
import re
from urllib.parse import urlparse
from bot.helper.ext_utils.files_utils import safe_path

USER_DEFAULTS = {
    'namespace': '', 'repo_name': '', 'repo_type': 'model', 'branch': 'main',
    'path': '', 'private': True, 'collision': 'rename', 'processing': 'none',
    'token': '', 'authorized': False, 'blocked': False,
}
BOT_DEFAULTS = {
    'public_mode': False, 'public_status': False, 'download_slots': 4,
    'upload_slots': 4, 'max_tasks': 40, 'max_file_gb': 20,
    'disk_reserve_mb': 1024, 'pipeline_files': 3, 'collection_files': 1000,
    'archive_gb': 80, 'archive_files': 10000, 'base_url': '',
}
CHOICES = {
    'repo_type': ['model', 'dataset'], 'private': [True, False],
    'collision': ['rename', 'overwrite', 'skip'],
    'processing': ['none', 'extract', 'zip', '7z'],
    'public_mode': [False, True], 'public_status': [False, True],
    'download_slots': [1, 2, 4, 6, 8], 'upload_slots': [1, 2, 4, 6, 8],
    'disk_reserve_mb': [512,1024,2048,4096], 'pipeline_files': [1,2,3,4], 'collection_files': [100,500,1000,2000],
    'max_tasks': [10, 20, 40, 80], 'max_file_gb': [2, 5, 10, 20, 50, 100],
    'archive_gb': [10, 25, 50, 80, 150], 'archive_files': [1000, 5000, 10000],
}
LABELS = {
    'token': 'HF token', 'namespace': 'Owner / organization', 'repo_name': 'Repository',
    'repo_type': 'Repository type', 'branch': 'Branch', 'path': 'Upload folder',
    'private': 'Private new repository', 'collision': 'Existing filenames',
    'processing': 'File processing', 'public_mode': 'Allow everyone',
    'public_status': 'Public website statistics', 'download_slots': 'Concurrent downloads',
    'upload_slots': 'Concurrent uploads', 'max_tasks': 'Active task limit',
    'max_file_gb': 'Maximum file size (GiB)', 'archive_gb': 'Expanded archive limit (GiB)',
    'disk_reserve_mb':'Keep disk free (MiB)', 'pipeline_files':'Files prepared per task',
    'collection_files':'Collection file limit', 'archive_files': 'Archive file limit', 'base_url': 'Website URL',
}

def validate(key, value):
    if key in CHOICES:
        if value not in CHOICES[key]:
            raise ValueError('Choose one of the available buttons.')
        return value
    value = str(value).strip()
    if key == 'token':
        if value and not re.fullmatch(r'hf_[A-Za-z0-9]{10,}', value):
            raise ValueError('Enter a Hugging Face token beginning with hf_.')
    elif key in ('namespace', 'repo_name'):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
            raise ValueError('Use letters, numbers, periods, underscores or hyphens.')
        if key == 'repo_name':
            from huggingface_hub.utils import validate_repo_id
            try:
                validate_repo_id(value)
            except ValueError:
                raise ValueError('Invalid repository name. Avoid repeated dots/hyphens and names ending in .git.') from None
    elif key == 'path':
        value = safe_path(value, empty=True)
    elif key == 'branch':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,95}', value) or '..' in value or value.endswith(('/', '.', '.lock')) or '//' in value or '/.' in value:
            raise ValueError('Invalid branch name.')
    elif key == 'base_url':
        p = urlparse(value)
        if value and (p.scheme != 'https' or not p.netloc or p.path not in ('', '/') or p.query or p.fragment or p.username):
            raise ValueError('Use your HTTPS website origin, without a path.')
        value = value.rstrip('/')
    else:
        raise ValueError('Unknown setting.')
    return value


def destination(profile):
    missing = [LABELS[k] for k in ('token', 'namespace', 'repo_name') if not profile.get(k)]
    if missing:
        raise ValueError('Set ' + ', '.join(missing) + ' in /usettings first.')
    return {k: profile[k] for k in ('namespace', 'repo_name', 'repo_type', 'branch', 'path', 'private', 'collision')}
