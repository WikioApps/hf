"""Stream decrypted MEGA content to disk; verify before exposing it for upload."""
from pathlib import Path
import time
import requests
from bot.helper.ext_utils.files_utils import check_space
from bot.helper.ext_utils.network_utils import public_url_sync
from .mega_crypto import Decoder,decode


def download(info,output,limit,report):
    details=info['mega']
    params={'id':1}
    command={'a':'g','g':1,'n' if details.get('folder') else 'p':details['handle']}
    if details.get('folder'):params['n']=details['folder']
    with requests.Session() as client:
        response=client.post('https://g.api.mega.co.nz/cs',params=params,json=[command],timeout=30)
        response.raise_for_status();data=response.json()
        data=data[0] if isinstance(data,list) and data else data
        if not isinstance(data,dict) or not data.get('g'):
            raise ValueError('MEGA download is unavailable or its transfer quota was reached.')
        size=int(data['s'])
        if size>limit:raise ValueError('MEGA file exceeds the available storage budget or file limit.')
        public_url_sync(data['g'])
        decoder=Decoder(decode(details['key']));done,last=0,0
        report(phase='Download',done=0,total=size)
        with client.get(data['g'],stream=True,timeout=(20,60)) as response,open(output,'wb') as file:
            if response.status_code==509:raise ValueError('MEGA transfer quota reached. Retry after the host resets it.')
            response.raise_for_status()
            for chunk in response.iter_content(1024**2):
                if not chunk:continue
                done+=len(chunk)
                if done>min(limit,size):raise ValueError('MEGA sent more data than expected.')
                check_space(Path(output).parent,len(chunk))
                file.write(decoder.update(chunk))
                if time.monotonic()-last>.3:
                    report(phase='Download',done=done,total=size);last=time.monotonic()
        if done!=size:raise ValueError('Incomplete MEGA download. Retry the task.')
        decoder.finish()
        report(phase='Download',done=done,total=size)
    return {'size':size}
