"""Archive metadata and streamed extraction using a local 7-Zip process."""
import os
from pathlib import Path
import subprocess
import time
import tarfile
import shutil
from bot.core.config_manager import ROOT
from bot.helper.ext_utils.files_utils import safe_path, check_space

BINARY = ROOT/'tools/7zz'


def entries(path, password='', max_files=10000, max_bytes=80*1024**3):
    if tarfile.is_tarfile(path):
        return tar_entries(path,max_files,max_bytes)
    args = [str(BINARY),'l','-slt','-ba','-sccUTF-8', '-p'+password, '--', str(path)]
    result = subprocess.run(args,capture_output=True,timeout=120)
    if result.returncode:
        raise ValueError('Cannot read archive. Check the password, format and missing volumes.')
    records, total = [], 0
    for block in result.stdout.decode('utf-8',errors='replace').strip().split('\n\n'):
        row = dict(line.split(' = ',1) for line in block.splitlines() if ' = ' in line)
        if not row.get('Path') or row.get('Folder') == '+' or row.get('Attributes','').startswith('D'):
            continue
        if (row.get('Symbolic Link') or row.get('Hard Link') or row.get('Mode','').startswith('l')
                or any(part.startswith('l') and len(part) >= 10 for part in row.get('Attributes','').split())):
            raise ValueError('Archive links are not supported.')
        name = row['Path'].replace('\\','/')
        while name.startswith('./'):
            name = name[2:]
        safe_path(name)
        length = int(row.get('Size','0'))
        total += length
        records.append({'name':name,'original':row['Path'],'size':length})
        if len(records) > max_files or total > max_bytes:
            raise ValueError('Archive exceeds the configured expanded-size or file-count limit.')
    if len({r['name'] for r in records}) != len(records):
        raise ValueError('Archive contains duplicate file paths.')
    if not records:
        raise ValueError('Archive contains no regular files.')
    return records


def extract(path, member, output, limit, password, report):
    if tarfile.is_tarfile(path):
        return tar_extract(path,member,output,limit,report)
    # -so writes only the selected member to our own file: archive paths cannot escape.
    args = [str(BINARY),'x','-so','-spd','-y','-p'+password,'--',str(path),member]
    process = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    done, last = 0, 0.0
    try:
        with open(output,'wb') as target:
            while chunk := process.stdout.read(1024**2):
                done += len(chunk)
                if done > limit:
                    raise ValueError('Expanded member exceeds the configured file size.')
                check_space(Path(output).parent,len(chunk))
                target.write(chunk)
                if time.monotonic()-last > .3:
                    report(phase='Extract',done=done)
                    last = time.monotonic()
        if process.wait():
            raise ValueError('Extraction failed. Verify the password and archive integrity.')
        report(phase='Extract',done=done)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def compress(source, output, kind, limit, report):
    args = [str(BINARY),'a','-t'+kind,'-mx=1','-mmt=2','-y',str(output),source.name]
    process = subprocess.Popen(args,cwd=source.parent,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            check_space(source.parent)
            if output.exists() and output.stat().st_size > limit:
                raise ValueError('Compressed output exceeds the file-size limit.')
            time.sleep(.5)
        if process.returncode:
            raise ValueError('Compression failed.')
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()


def tar_entries(path,max_files,max_bytes):
    records,total = [],0
    with tarfile.open(path,'r|*') as archive:
        for member in archive:
            if not member.isfile():
                if member.issym() or member.islnk():
                    raise ValueError('Archive links are not supported.')
                continue
            name = member.name
            while name.startswith('./'): name=name[2:]
            name = safe_path(name)
            total += member.size
            records.append({'name':name,'original':member.name,'size':member.size})
            if len(records)>max_files or total>max_bytes:
                raise ValueError('Archive exceeds the expanded-size or file-count limit.')
    if not records or len({r['name'] for r in records}) != len(records):
        raise ValueError('Archive is empty or has duplicate paths.')
    return records


def tar_extract(path,member,output,limit,report):
    with tarfile.open(path,'r|*') as archive:
        for info in archive:
            if info.name != member: continue
            if not info.isfile() or info.size>limit:
                raise ValueError('Invalid member type or size.')
            done,last = 0,0
            with archive.extractfile(info) as source,open(output,'wb') as target:
                while chunk:=source.read(1024**2):
                    check_space(Path(output).parent,len(chunk))
                    target.write(chunk); done+=len(chunk)
                    if time.monotonic()-last>.3:
                        report(phase='Extract',done=done,total=info.size);last=time.monotonic()
            report(phase='Extract',done=done,total=info.size)
            return
    raise ValueError('Archive member is missing.')
