"""Cookie-free yt-dlp and gallery-dl catalogues, with real media transfer hooks."""
import logging
import os
from pathlib import Path
import time
from urllib.parse import urlparse
from bot.helper.ext_utils.files_utils import filename,check_space
from bot.helper.ext_utils.network_utils import public_url_sync

LOGGER=logging.getLogger(__name__)


class Quiet:
    def debug(self,*args):pass
    def warning(self,message):LOGGER.warning('%s',message)
    def error(self,message):LOGGER.error('%s',message)


def options():
    import imageio_ffmpeg
    from bot.core.config_manager import ROOT
    location=str(ROOT/'tools') if (ROOT/'tools/ffmpeg').is_file() else imageio_ffmpeg.get_ffmpeg_exe()
    return {'quiet':True,'logger':Quiet(),'cachedir':False,'socket_timeout':30,
            'retries':3,'fragment_retries':3,'concurrent_fragment_downloads':1,
            'ffmpeg_location':location,'noprogress':True,
            'ignoreerrors':False,'allow_unplayable_formats':False,'nopart':False}


def gallery_catalog(url,limit):
    from gallery_dl import config,extractor,job
    config.clear()
    config.set(('extractor',),'timeout',30)
    config.set(('extractor',),'retries',2)
    config.set(('extractor',),'cookies',None)
    config.set(('extractor',),'cookies-from-browser',None)
    config.set(('extractor',),'user-agent','Mozilla/5.0')
    if not extractor.find(url):return None
    items,seen=[],set()
    class Catalog(job.Job):
        def __init__(self,url,parent=None):
            self.depth=parent.depth+1 if parent else 0
            if self.depth>12:raise ValueError('Gallery nesting is too deep.')
            super().__init__(url,parent)
        def handle_url(self,target,meta):
            public_url_sync(target)
            if len(items)>=limit:raise ValueError('Collection exceeds the configured file limit.')
            base=meta.get('filename') or meta.get('id') or str(len(items)+1)
            extension=str(meta.get('extension',''))
            name=filename(str(base)+(('.'+extension) if extension and not str(base).endswith('.'+extension) else ''))
            headers=dict(self.extractor.session.headers)
            headers.update(meta.get('_http_headers') or {})
            # Only automatically acquired guest cookies that apply to this file's domain.
            from requests import Request
            prepared=self.extractor.session.prepare_request(Request('GET',target,headers=headers))
            safe={k:v for k,v in prepared.headers.items() if k.lower() in ('user-agent','referer','cookie','accept')}
            items.append({'url':target,'name':name,'engine':'aria2','headers':safe,'total':int(meta.get('filesize') or 0)})
        def handle_queue(self,target,meta):
            if target in seen:return
            if len(seen)>=limit:raise ValueError('Collection exceeds the configured page limit.')
            public_url_sync(target);seen.add(target)
            child=Catalog(target,self)
            if child.run():raise ValueError('A gallery page is unavailable without login or cookies.')
    runner=Catalog(url)
    if runner.run():raise ValueError('This gallery could not be resolved without login, cookies, or a browser challenge.')
    return {'items':items,'title':runner.extractor.category+' collection'}


def video_catalog(url,limit):
    import yt_dlp
    with yt_dlp.YoutubeDL({**options(),'extract_flat':'in_playlist','playlistend':limit+1}) as client:
        info=client.extract_info(url,download=False)
    items=[]
    def collect(info,depth=0):
        if depth>10:raise ValueError('Playlist nesting is too deep.')
        if info.get('entries') is not None:
            for child in info['entries']:
                if child:collect(child,depth+1)
            return
        if len(items)>=limit:raise ValueError('Playlist exceeds the configured file limit.')
        target=info.get('webpage_url') or info.get('url') or url
        public_url_sync(target)
        ext=info.get('ext') or 'mp4'
        name=filename(str(info.get('title') or info.get('id') or 'media')+(' ['+str(info['id'])+']' if info.get('id') else '')+'.'+ext)
        items.append({'engine':'yt_dlp','url':target,'name':name,'total':0})
    collect(info)
    return {'items':items,'title':str(info.get('title') or 'Media')}


def drive_catalog(url,limit):
    import gdown,tempfile
    with tempfile.TemporaryDirectory(prefix='hf-drive-') as directory:
        files=gdown.download_folder(url=url,output=directory,quiet=True,use_cookies=False,skip_download=True)
        if not files:raise ValueError('Google Drive folder is private, empty, or blocked by its quota.')
        if len(files)>limit:raise ValueError('Drive folder exceeds the configured file limit.')
        items=[{'engine':'gdown','url':'https://drive.google.com/uc?id='+item.id,
                'name':'/'.join(filename(p) for p in Path(item.path).parts),'total':0} for item in files]
    return {'items':items,'title':'Google Drive folder'}


def catalog(url,engine,limit):
    public_url_sync(url)
    if engine=='drive_folder':return drive_catalog(url,limit)
    if engine!='video':
        try:
            result=gallery_catalog(url,limit)
            if result is not None:return result
        except ValueError:
            if engine=='gallery':raise
            LOGGER.info('Gallery extraction unavailable; trying the public video extractor')
        if engine=='gallery':raise ValueError('This gallery URL is not supported by the installed extractor.')
    return video_catalog(url,limit)


def download(url,output,limit,report):
    import yt_dlp
    output=Path(output);directory=output.parent
    finished=[];sizes={};last=0
    def hook(data):
        nonlocal last
        if data.get('status') not in ('downloading','finished'):return
        path=data.get('tmpfilename') or data.get('filename') or 'media'
        sizes[path]=int(data.get('downloaded_bytes') or 0)
        total=int(data.get('total_bytes') or data.get('total_bytes_estimate') or 0)
        used=sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        if max(used,sum(sizes.values()),total)>limit:
            raise ValueError('Media exceeds the reserved disk budget or configured file limit.')
        check_space(directory)
        if time.monotonic()-last>.3 or data['status']=='finished':
            report(phase='Download',done=sum(sizes.values()),total=0)
            last=time.monotonic()
    def moved(data):
        if data.get('status')=='finished' and data.get('info_dict',{}).get('filepath'):
            finished.append(data['info_dict']['filepath'])
    opts={**options(),'noplaylist':True,'format':'bestvideo+bestaudio/best','merge_output_format':'mkv',
          'outtmpl':str(directory/'media.%(ext)s'),'progress_hooks':[hook],
          'postprocessor_hooks':[moved],'max_filesize':limit,'overwrites':True}
    with yt_dlp.YoutubeDL(opts) as client:
        info=client.extract_info(url,download=True)
        candidate=Path(info.get('filepath') or (finished[-1] if finished else client.prepare_filename(info)))
    if not candidate.is_file():
        candidates=[p for p in directory.glob('media.*') if p.suffix not in ('.part','.ytdl','.temp')]
        if len(candidates)!=1:raise ValueError('Media engine did not produce one complete output file.')
        candidate=candidates[0]
    if candidate.stat().st_size>limit:raise ValueError('Final media exceeds the reserved disk budget.')
    if candidate!=output:candidate.replace(output)
    name=filename(str(info.get('title') or info.get('id') or 'media')+(' ['+str(info['id'])+']' if info.get('id') else '')+candidate.suffix)
    report(phase='Download',done=output.stat().st_size,total=output.stat().st_size)
    return {'size':output.stat().st_size,'name':name}
