"""HF transport progress is separate from hashing and commit finalization."""
import contextlib
import io
import os
from pathlib import PurePosixPath
import time
from urllib.parse import quote


def api_client(token):
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '0'
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    from huggingface_hub import HfApi, configure_http_backend, constants
    # Hub constants may already be imported by another module in this worker.
    constants.HF_HUB_DISABLE_XET = True
    constants.HF_HUB_ENABLE_HF_TRANSFER = False
    import requests
    class Session(requests.Session):
        def request(self,*args,**kwargs):
            kwargs.setdefault('timeout',(20,120))
            return super().request(*args,**kwargs)
    configure_http_backend(backend_factory=Session)
    return HfApi(token=token)


def install_progress(report, total):
    import huggingface_hub._commit_api as commit_api
    class Progress(io.BufferedReader):
        def __init__(self,raw):
            super().__init__(raw)
            self.last = 0
            self.high_water = 0
        def read(self,length=-1):
            data = super().read(length)
            self.high_water = max(self.high_water,self.tell())
            if time.monotonic()-self.last > .3 or self.tell() == total:
                report(phase='Upload',done=self.high_water,total=total)
                self.last = time.monotonic()
            return data
    @contextlib.contextmanager
    def stream(path):
        with Progress(io.FileIO(path,'r')) as file:
            yield file
    commit_api.tqdm_stream_file = stream


def remote_name(api, repo, dest, name):
    if dest['collision'] == 'overwrite':
        return name, False
    exists = api.file_exists(repo,name,repo_type=dest['repo_type'],revision=dest['branch'])
    if not exists or dest['collision'] == 'skip':
        return name, exists
    path = PurePosixPath(name)
    for number in range(1,10000):
        candidate = str(path.with_name(f'{path.stem} ({number}){path.suffix}'))
        if not api.file_exists(repo,candidate,repo_type=dest['repo_type'],revision=dest['branch']):
            return candidate,False
    raise ValueError('Too many existing files with the same name.')


def upload(path, name, payload, report):
    dest, token = payload['destination'], payload['token']
    api = api_client(token)
    from huggingface_hub import CommitOperationAdd
    repo = dest['namespace']+'/'+dest['repo_name']
    report(phase='Prepare',done=0,total=os.path.getsize(path))
    api.create_repo(repo,repo_type=dest['repo_type'],private=dest['private'],exist_ok=True)
    if dest['branch'] != 'main':
        api.create_branch(repo,branch=dest['branch'],repo_type=dest['repo_type'],exist_ok=True)
    remote, skipped = remote_name(api,repo,dest,name)
    prefix = 'datasets/' if dest['repo_type'] == 'dataset' else ''
    url = f'https://huggingface.co/{prefix}{repo}/blob/{quote(dest["branch"],safe="")}/{quote(remote,safe="/")}'
    if skipped:
        return {'path':remote,'url':url,'size':os.path.getsize(path),'skipped':True}
    install_progress(report,os.path.getsize(path))
    report(phase='Hash',done=0,total=os.path.getsize(path))
    operation = CommitOperationAdd(path_in_repo=remote,path_or_fileobj=path)
    report(phase='Prepare',done=0,total=os.path.getsize(path))
    api.preupload_lfs_files(repo,[operation],repo_type=dest['repo_type'],revision=dest['branch'],num_threads=1)
    report(phase='Commit')
    for attempt in range(4):
        try:
            commit = api.create_commit(repo_id=repo,repo_type=dest['repo_type'],revision=dest['branch'],
                                       operations=[operation],commit_message=f'Upload {remote}')
            return {'path':remote,'url':url,'size':os.path.getsize(path),'commit':commit.oid,'skipped':False}
        except Exception as error:
            if getattr(getattr(error,'response',None),'status_code',None) not in (409,429,502,503) or attempt == 3:
                raise
            time.sleep(2**attempt)
