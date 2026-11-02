"""ZIP metadata, bounded extraction and compression without external binaries."""
from pathlib import Path
import stat
import time
import zipfile
from bot.helper.ext_utils.files_utils import check_space, safe_path

SUPPORTED = {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED,zipfile.ZIP_BZIP2,zipfile.ZIP_LZMA}


def entries(path,max_files,max_bytes):
    records,total = [],0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir(): continue
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Archive links are not supported.')
            name = member.filename
            while name.startswith('./'): name=name[2:]
            name = safe_path(name)
            total += member.file_size
            records.append({'name':name,'original':member.filename,'size':member.file_size})
            if len(records)>max_files or total>max_bytes:
                raise ValueError('Archive exceeds the expanded-size or file-count limit.')
    if not records or len({r['name'] for r in records}) != len(records):
        raise ValueError('Archive is empty or has duplicate paths.')
    return records


def extract(path,member,output,limit,password,report):
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member)
        if info.compress_type not in SUPPORTED or info.flag_bits & 64:
            return False
        if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('Only regular archive files can be extracted.')
        if info.file_size>limit:
            raise ValueError('Expanded member exceeds the configured file size.')
        try:
            with archive.open(info,pwd=password.encode() if password else None) as source,open(output,'wb') as target:
                copy(source,target,Path(output).parent,limit,info.file_size,report)
        except RuntimeError as error:
            raise ValueError('ZIP extraction needs the correct password. Use -pw "password".') from error
        except zipfile.BadZipFile as error:
            raise ValueError('ZIP integrity check failed. The archive may be damaged or incomplete.') from error
    return True


def copy(source,target,folder,limit,total,report,phase='Extract'):
    done,last = 0,0.0
    while chunk := source.read(1024**2):
        done += len(chunk)
        if done>limit:
            raise ValueError('Expanded member exceeds the configured file size.')
        check_space(folder,len(chunk))
        target.write(chunk)
        if time.monotonic()-last>.3:
            report(phase=phase,done=done,total=total)
            last = time.monotonic()
    if done != total:
        raise ValueError('Archive member is incomplete.')
    report(phase=phase,done=done,total=total)


def compress(source,output,limit,report):
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        with source.open('rb') as reader,archive.open(source.name,'w',force_zip64=True) as writer:
            copy(reader,writer,source.parent,source.stat().st_size,source.stat().st_size,report,phase='Archive')
    if output.stat().st_size>limit:
        raise ValueError('Compressed output exceeds the file-size limit.')
