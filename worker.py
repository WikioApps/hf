"""Isolated, cancellable Python workers. JSON on stdout; no credentials in arguments."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import time
import zipfile


def emit(**data):
    print(json.dumps(data), flush=True)


def member_path(name):
    from config import safe_path
    while name.startswith('./'): name = name[2:]
    return safe_path(name.rstrip('/'))


def archive_members(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as a:
            return [(member_path(m.filename), m.file_size) for m in a.infolist()
                    if not m.is_dir() and not stat.S_ISLNK(m.external_attr >> 16)]
    if str(path).lower().endswith('.7z'):
        import py7zr
        with py7zr.SevenZipFile(path) as a:
            if any(getattr(f, 'is_symlink', False) or getattr(f, 'is_junction', False) for f in a.files):
                raise ValueError('Archive links are not supported')
            return [(member_path(m.filename), m.uncompressed) for m in a.list() if not m.is_directory]
    if str(path).lower().endswith('.rar'):
        raise ValueError('RAR extraction requires an external decoder. Send ZIP/TAR/7Z or upload the RAR intact.')
    with tarfile.open(path, 'r:*') as a:
        return [(member_path(m.name), m.size) for m in a if m.isfile()]


def extract(path, member, target, limit):
    from config import safe_path
    safe_path(member)
    done, last = 0, 0.0
    def copy(source, out):
        nonlocal done, last
        while chunk := source.read(1024 * 1024):
            done += len(chunk)
            if done > limit:
                raise ValueError('Extracted member exceeds configured limit')
            out.write(chunk)
            if time.monotonic() - last > .25:
                emit(done=done, phase='Extract'); last = time.monotonic()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as a:
            info = next(m for m in a.infolist() if not m.is_dir() and member_path(m.filename) == member)
            with a.open(info) as source, open(target, 'wb') as out: copy(source, out)
    elif str(path).lower().endswith('.7z'):
        import py7zr
        from py7zr.io import Py7zIO, WriterFactory
        class Writer(Py7zIO):
            def __init__(self): self.f = open(target, 'w+b')
            def write(self, data):
                if self.f.tell() + len(data) > limit: raise ValueError('Extracted file exceeds limit')
                n = self.f.write(data); emit(done=self.f.tell(), phase='Extract'); return n
            def read(self, size=None): return self.f.read(-1 if size is None else size)
            def seek(self, offset, whence=0): return self.f.seek(offset, whence)
            def flush(self): self.f.flush()
            def size(self): return os.fstat(self.f.fileno()).st_size
        class Factory(WriterFactory):
            def __init__(self): self.writer = Writer()
            def create(self, name):
                if member_path(name) != member: raise ValueError('Unexpected archive member')
                return self.writer
        factory = Factory()
        try:
            with py7zr.SevenZipFile(path) as a:
                original = next(m.filename for m in a.list() if not m.is_directory and member_path(m.filename) == member)
                a.extract(targets=[original], factory=factory)
        finally:
            factory.writer.f.close()
    else:
        with tarfile.open(path, 'r:*') as a:
            m = next(m for m in a if m.isfile() and member_path(m.name) == member)
            if not m.isfile(): raise ValueError('Not a regular file')
            with a.extractfile(m) as source, open(target, 'wb') as out:
                copy(source, out)
    emit(done=os.path.getsize(target), phase='Extract')


def upload(path, remote):
    # Keep transfers observable and cancellable in Python (no Xet/hf_transfer subprocess).
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '0'
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    from huggingface_hub import HfApi, CommitOperationAdd, configure_http_backend
    import requests
    class TimedSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault('timeout', (20, 120))
            return super().request(*args, **kwargs)
    configure_http_backend(backend_factory=TimedSession)
    import huggingface_hub._commit_api as commit_api
    size = os.path.getsize(path)
    class ProgressFile(io.BufferedReader):
        def __init__(self, raw):
            super().__init__(raw); self.last = 0.0
        def read(self, n=-1):
            data = super().read(n)
            now = time.monotonic()
            if now - self.last >= .25 or self.tell() == size:
                emit(phase='Upload', done=self.tell(), total=size); self.last = now
            return data
    @contextlib.contextmanager
    def stream(filename):
        with ProgressFile(io.FileIO(filename, 'r')) as f:
            yield f
    # This hook is intentionally pinned/tested against huggingface_hub 0.36.0.
    # It runs ONLY for LFS transport, never for the pre-upload hashing pass.
    commit_api.tqdm_stream_file = stream
    api = HfApi(token=os.environ['HF_TOKEN'])
    repo, kind, branch = os.environ['REPO_ID'], os.getenv('REPO_TYPE', 'model'), os.getenv('BRANCH', 'main')
    emit(phase='Prepare', total=size, done=0)
    op = CommitOperationAdd(path_in_repo=remote, path_or_fileobj=path)
    api.preupload_lfs_files(repo_id=repo, additions=[op], repo_type=kind, revision=branch, num_threads=1)
    emit(phase='Commit', total=size)
    info = api.create_commit(repo_id=repo, repo_type=kind, revision=branch,
                             operations=[op], commit_message=f'Upload {remote}')
    emit(result={'commit': info.oid, 'remote': remote})


def main():
    mode, *args = sys.argv[1:]
    if mode == 'upload': upload(*args)
    elif mode == 'list':
        members = archive_members(args[0]); emit(result=members)
    elif mode == 'extract': extract(args[0], args[1], args[2], int(args[3]))
    elif mode == 'drive':
        import gdown
        import importlib
        gd = importlib.import_module('gdown.download')
        original_name = gd._get_filename_from_response
        def capture_name(response):
            name = original_name(response)
            if name: emit(name=name)
            return name
        gd._get_filename_from_response = capture_name
        path = gdown.download(args[0], args[1], quiet=True, fuzzy=True, resume=True, use_cookies=False)
        if not path: raise ValueError('Google Drive did not return a downloadable file')
        emit(result=path)
    elif mode == 'resolve':
        import yt_dlp
        class Quiet:
            def debug(self, *a): pass
            def warning(self, *a): pass
            def error(self, *a): pass
        with yt_dlp.YoutubeDL({'quiet': True, 'logger': Quiet(), 'noplaylist': True,
                              'skip_download': True, 'socket_timeout': 30,
                              'format': 'best[protocol=https]/best[protocol=http]',
                              'cachedir': False}) as y:
            info = y.extract_info(args[0], download=False)
            if info.get('entries'): raise ValueError('Send an individual file/video URL, not a playlist')
            url = info.get('url', '')
            if not url.startswith(('https://', 'http://')): raise ValueError('No single-file HTTP format available')
            emit(result={'url': url, 'name': y.prepare_filename(info), 'headers': info.get('http_headers', {})})
    else: raise ValueError('Unknown worker command')


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        # URLs/tokens may occur in exception strings. Report the type; parent logs task ID.
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        code = f' (HTTP {status})' if status else ''
        emit(error=f'{type(exc).__name__}{code}: operation failed; check source access, format, token scope and available storage')
        sys.exit(1)
