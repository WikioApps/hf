"""Install the official 7-Zip binary into this checkout; no system changes."""
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RELEASE = '26.05'
ASSETS = {
    'x86_64': ('7z2605-linux-x64.tar.xz','22cf82fcc6eb7474f1d5bb4f02c0f8ebd573a718089d6833dc30f8350a3b232e'),
    'aarch64': ('7z2605-linux-arm64.tar.xz','0b71f6cfc34669b3bf69f2347efd3429f77020665003a13b9cdc53b784270421'),
}

def install():
    if platform.system() != 'Linux' or platform.machine() not in ASSETS:
        raise SystemExit('The archive engine requires Linux x86_64 or aarch64.')
    target = ROOT/'tools/7zz'
    marker = ROOT/'tools/version'
    if not target.exists() or not marker.exists() or marker.read_text() != RELEASE:
        name, digest = ASSETS[platform.machine()]
        url = f'https://github.com/ip7z/7zip/releases/download/{RELEASE}/{name}'
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read(20*1024**2)
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit('7-Zip checksum verification failed.')
        target.parent.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:xz') as archive:
            with archive.extractfile('7zz') as source:
                target.write_bytes(source.read())
            for item in archive.getmembers():
                if item.isfile() and Path(item.name).name.lower() in ('license.txt','copying.txt'):
                    (target.parent/Path(item.name).name).write_bytes(archive.extractfile(item).read())
        target.chmod(0o755)
        marker.write_text(RELEASE)
    subprocess.run([str(target),'i'],check=True,stdout=subprocess.DEVNULL)
    binary = shutil.which('aria2c')
    if not binary:
        raise SystemExit('aria2c is missing from the virtual environment.')
    subprocess.run([binary,'--version'],check=True,stdout=subprocess.DEVNULL)
    print('aria2 and 7-Zip are ready.')

if __name__ == '__main__':
    install()
