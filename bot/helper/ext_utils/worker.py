"""JSON worker protocol. Secrets arrive over stdin, never command-line arguments."""
import json
import logging
import os
from pathlib import Path
import sys
import time
from bot.helper.ext_utils.files_utils import filename, check_space
from bot.helper.ext_utils.log_utils import configure, register_secrets, redact


def report(**data):
    print(json.dumps(data),flush=True)


def resolve(url):
    import yt_dlp
    class Quiet:
        def debug(self,*args): pass
        def warning(self,*args): pass
        def error(self,*args): pass
    options = {'quiet':True,'logger':Quiet(),'noplaylist':True,'cachedir':False,
               'socket_timeout':30,'format':'best[protocol=https]/best[protocol=http]'}
    with yt_dlp.YoutubeDL(options) as client:
        info = client.extract_info(url,download=False)
        if info.get('entries') or not info.get('url','').startswith(('http://','https://')):
            raise ValueError('No single-file HTTP format is available for this URL.')
        return {'url':info['url'],'name':filename(client.prepare_filename(info)),
                'headers':info.get('http_headers',{}),'total':info.get('filesize') or 0}


def drive(url, output, limit):
    import gdown
    import importlib
    module = importlib.import_module('gdown.download')
    original = module._get_filename_from_response
    def capture(response):
        name = original(response)
        if name: report(name=filename(name))
        return name
    module._get_filename_from_response = capture
    class Writer:
        def __init__(self):
            self.file = open(output,'wb'); self.last = 0
        def write(self,data):
            if self.file.tell()+len(data) > limit: raise ValueError('File exceeds the configured limit.')
            check_space(Path(output).parent,len(data))
            count = self.file.write(data)
            if time.monotonic()-self.last > .3:
                report(phase='Download',done=self.file.tell()); self.last=time.monotonic()
            return count
        def __getattr__(self,name): return getattr(self.file,name)
    writer = Writer()
    try:
        if not gdown.download(url,writer,quiet=True,fuzzy=True,use_cookies=False):
            raise ValueError('Google Drive did not return a downloadable file.')
    finally:
        writer.file.close()
    return {'size':os.path.getsize(output)}


def main():
    mode,*args = sys.argv[1:]
    payload = json.loads(sys.stdin.readline() or '{}')
    register_secrets(payload.get('token'),payload.get('password'))
    logging.info('Worker started operation=%s',mode)
    if mode == 'upload':
        from bot.helper.mirror_leech_utils.upload_utils.huggingface_upload import upload
        result = upload(args[0],args[1],payload,report)
    elif mode == 'inspect_hf':
        from bot.helper.mirror_leech_utils.upload_utils.huggingface_upload import api_client
        result = api_client(payload['token']).whoami()
        result = {'name':result['name'],'organizations':[o['name'] for o in result.get('orgs',[])]}
    elif mode == 'branches':
        from bot.helper.mirror_leech_utils.upload_utils.huggingface_upload import api_client
        result = api_client(payload['token']).list_repo_refs(payload['repo'],repo_type=payload['repo_type'])
        result = [branch.name for branch in result.branches]
    elif mode == 'resolve':
        result = resolve(args[0])
    elif mode == 'drive':
        result = drive(args[0],args[1],int(args[2]))
    elif mode in ('list','extract','compress'):
        from bot.helper.mirror_leech_utils.archive_utils import sevenz
        if mode == 'list':
            result = sevenz.entries(args[0],payload.get('password',''),payload['max_files'],payload['max_bytes'])
        elif mode == 'extract':
            sevenz.extract(args[0],args[1],args[2],int(args[3]),payload.get('password',''),report)
            result = {'size':os.path.getsize(args[2])}
        else:
            sevenz.compress(Path(args[0]),Path(args[1]),args[2],int(args[3]),report)
            result = {'size':os.path.getsize(args[1])}
    elif mode == 'speedtest':
        from speedtest import Speedtest
        logging.info('Speedtest: retrieving configuration')
        client = Speedtest(secure=True,timeout=15)
        logging.info('Speedtest: selecting the best server')
        client.get_best_server()
        logging.info('Speedtest: measuring download')
        client.download(threads=2)
        logging.info('Speedtest: measuring upload')
        client.upload(threads=2)
        try:
            logging.info('Speedtest: requesting the official result image')
            client.results.share()
        except Exception:
            logging.exception('Speedtest share image unavailable; measured results retained')
        raw = client.results.dict()
        result = {key:raw.get(key) for key in ('download','upload','ping','timestamp','bytes_sent','bytes_received','share')}
        result['server'] = {key:raw.get('server',{}).get(key) for key in ('name','country','cc','sponsor','latency','lat','lon')}
    else:
        raise ValueError('Unknown worker operation.')
    report(result=result)

if __name__ == '__main__':
    configure()
    try:
        main()
    except Exception as error:
        logging.exception('Worker failed')
        status = getattr(getattr(error,'response',None),'status_code',None)
        message = str(error) if type(error) is ValueError and len(str(error)) < 250 and 'http' not in str(error).lower() else type(error).__name__
        if isinstance(error,FileNotFoundError):
            message = 'Required local file or executable is missing: '+Path(error.filename or 'unknown').name+'. Check /logs and the build output.'
        report(error=redact(message)+(f' (HTTP {status})' if status else ''))
        raise SystemExit(1)
