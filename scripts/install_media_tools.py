"""Install media helpers into tools/ without root or a system package manager."""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

RELEASE='autobuild-2026-11-02-12-51'
ASSETS={
 'x86_64':('ffmpeg-n9.0.2-45-g1e65c6cd5d-linux64-lgpl-9.0.tar.xz','95e563328196db8b0f6247ac5197dae3311997646faf1c5f1be4caec9a3b3650'),
 'aarch64':('ffmpeg-n9.0.2-45-g1e65c6cd5d-linuxarm64-lgpl-9.0.tar.xz','9d108f86231fd345ce2248a0370baf8aa48e33bed425b5abd91fdec85caf2e34'),
}


def install(directory):
    import imageio_ffmpeg
    directory.mkdir(exist_ok=True)
    ffmpeg=directory/'ffmpeg'
    ffmpeg.unlink(missing_ok=True)
    ffmpeg.symlink_to(Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve())
    target=directory/'ffprobe';marker=directory/'ffprobe.version'
    if not target.is_file() or not marker.is_file() or marker.read_text()!=RELEASE:
        asset,expected=ASSETS[platform.machine()]
        url=f'https://github.com/BtbN/FFmpeg-Builds/releases/download/{RELEASE}/{asset}'
        with tempfile.TemporaryFile() as download:
            digest=hashlib.sha256();length=0
            with urllib.request.urlopen(url,timeout=90) as response:
                while chunk:=response.read(1024**2):
                    length+=len(chunk)
                    if length>200*1024**2:raise SystemExit('FFprobe archive exceeds the expected download size.')
                    digest.update(chunk);download.write(chunk)
            if digest.hexdigest()!=expected:raise SystemExit('FFprobe checksum verification failed.')
            download.seek(0)
            with tarfile.open(fileobj=download,mode='r|xz') as archive:
                found=False
                for member in archive:
                    if not member.isfile():continue
                    if member.name.endswith('/bin/ffprobe'):
                        temporary=target.with_suffix('.new')
                        with archive.extractfile(member) as source,temporary.open('wb') as output:
                            shutil.copyfileobj(source,output)
                        temporary.chmod(0o755);temporary.replace(target);found=True
                    elif Path(member.name).name.lower().startswith(('license','copying')):
                        (directory/('ffprobe-'+Path(member.name).name)).write_bytes(archive.extractfile(member).read())
                if not found:raise SystemExit('The pinned FFprobe archive did not contain its executable.')
        marker.write_text(RELEASE)
    for tool in (ffmpeg,target):
        subprocess.run([str(tool),'-version'],check=True,stdout=subprocess.DEVNULL)
