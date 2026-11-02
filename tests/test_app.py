import asyncio
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import zipfile

from config import Config, safe_path
from core import Job, Manager
from bot import parse_line, task_text
from app import Application
from transfers import download_http, public_url, worker as run_worker
import worker


def config(root):
    return Config(123,'a'*32,'123:abc',123,'hf_test','test/repo',frozenset({123}),data=Path(root),reserve=0)


class ConfigTests(unittest.TestCase):
    def test_missing_env(self):
        with patch.dict(os.environ,{},clear=True),patch('dotenv.load_dotenv'):
            with self.assertRaisesRegex(ValueError,'Missing required'): Config.load()
    def test_port_and_env(self):
        env={'API_ID':'123','API_HASH':'a'*32,'BOT_TOKEN':'123:abc','OWNER_ID':'123','HF_TOKEN':'hf_test','REPO_ID':'test/repo','PORT':'9123'}
        with patch.dict(os.environ,env,clear=True),patch('dotenv.load_dotenv'):
            self.assertEqual(Config.load().port,9123)
            os.environ['PORT']='0'
            with self.assertRaisesRegex(ValueError,'PORT'): Config.load()
    def test_traversal(self):
        for path in ('../x','/etc/passwd','a/../b','a//b','a\\..\\b'):
            with self.assertRaises(ValueError): safe_path(path)
    def test_parser(self):
        src,opts=parse_line('https://example.com/a -n "some file.zip" -unzip -s -p weights')
        self.assertEqual(opts['name'],'some file.zip');self.assertTrue(opts['select']);self.assertTrue(opts['extract'])
        with self.assertRaises(ValueError): parse_line('https://example.com -s')
    def test_status_real_phase(self):
        j=Job('id',1,2,3,'url',Path('/tmp'),name='<x>');j.stage('Download',100);j.advance(25)
        self.assertIn('25.00%',task_text(j));self.assertIn('&lt;x&gt;',task_text(j))
        j.stage('Prepare',100);self.assertNotIn('25.00%',task_text(j));self.assertNotIn('Processed',task_text(j))


class ArchiveTests(unittest.TestCase):
    def test_zip_nested(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.zip';out=Path(d)/'out'
            with zipfile.ZipFile(p,'w') as a: a.writestr('folder/file.bin',b'123456')
            self.assertEqual(worker.archive_members(p),[('folder/file.bin',6)])
            worker.extract(p,'folder/file.bin',out,10);self.assertEqual(out.read_bytes(),b'123456')
            with self.assertRaises(ValueError):worker.extract(p,'folder/file.bin',out,3)
    def test_zip_slip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.zip'
            with zipfile.ZipFile(p,'w') as a:a.writestr('../escape',b'x')
            with self.assertRaises(ValueError):worker.archive_members(p)
    def test_tar_links_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.tar.gz';out=Path(d)/'out'
            with tarfile.open(p,'w:gz') as a:
                info=tarfile.TarInfo('dir/f');info.size=3;a.addfile(info,io.BytesIO(b'abc'))
                info=tarfile.TarInfo('link');info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';a.addfile(info)
            self.assertEqual(worker.archive_members(p),[('dir/f',3)])
            worker.extract(p,'dir/f',out,3);self.assertEqual(out.read_bytes(),b'abc')
    def test_7z(self):
        import py7zr
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.7z';out=Path(d)/'out'
            with py7zr.SevenZipFile(p,'w') as a:a.writestr(b'hello','dir/test.txt')
            self.assertEqual(worker.archive_members(p),[('dir/test.txt',5)])
            worker.extract(p,'dir/test.txt',out,10);self.assertEqual(out.read_bytes(),b'hello')


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_ssrf(self):
        for url in ('file:///etc/passwd','http://127.0.0.1/a','http://[::1]/','http://169.254.169.254/'):
            with self.assertRaises(ValueError): await public_url(url)
    async def test_health(self):
        app=Application(config('/tmp'))
        async def request(path):
            sent=[]
            async def send(d):sent.append(d)
            await app({'type':'http','path':path},None,send);return sent[0]['status']
        self.assertEqual(await request('/healthz'),503)
        app.bot=MagicMock();app.bot.healthy.return_value=True
        self.assertEqual(await request('/healthz'),200)
        app.bot.healthy.return_value=False
        self.assertEqual(await request('/'),503);self.assertEqual(await request('/missing'),404)
    async def test_failure_retains_and_retry(self):
        with tempfile.TemporaryDirectory() as d:
            m=Manager(config(d),None)
            async def dl(j,*args):
                (j.directory/'source').write_bytes(b'hello');return j.directory/'source'
            async def upload(j,mode,*args,**kw):
                if mode=='upload':raise ValueError('temporary failure')
            with patch('core.download',dl),patch('core.worker',upload):
                j=m.add(123,1,2,'https://example.com/a',name='test.bin')
                await j.runner
            self.assertEqual(j.phase,'Failed');self.assertTrue((j.directory/'source').exists())
            m2=Manager(config(d),None);j2=m2.jobs[j.id]
            async def success(j,mode,*args,**kw):return {'commit':'a'*40,'remote':args[1]}
            with patch('core.download',side_effect=AssertionError('must reuse source')),patch('core.worker',success):
                await m2.retry(j2);await j2.runner
            self.assertEqual(j2.phase,'Complete');self.assertFalse((j2.directory/'source').exists())
            self.assertIn('test.bin',j2.uploaded)
    async def test_cancel_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            m=Manager(config(d),None)
            async def dl(j,*args):await asyncio.sleep(100)
            with patch('core.download',dl):
                a=m.add(123,1,1,'x');b=m.add(123,1,2,'y');await asyncio.sleep(.01)
                await m.cancel(a);self.assertEqual(a.phase,'Cancelled');self.assertNotEqual(b.phase,'Cancelled')
                await m.close();self.assertEqual(b.phase,'Interrupted')
    async def test_real_worker_extract(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config(d);p=Path(d)/'a.zip';out=Path(d)/'out'
            with zipfile.ZipFile(p,'w') as a:a.writestr('hello.txt',b'hello')
            j=Job('id',1,1,1,'url',Path(d));j.stage('Extract',5)
            result=await run_worker(j,'list',p,cfg=cfg)
            self.assertEqual(result,[['hello.txt',5]])
            await run_worker(j,'extract',p,'hello.txt',out,10,cfg=cfg)
            self.assertEqual(out.read_bytes(),b'hello');self.assertEqual(j.done,5)


class HFTransportTests(unittest.TestCase):
    def test_upload_progress_only_transport_and_commit(self):
        from huggingface_hub import CommitOperationAdd
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data';p.write_bytes(b'x'*1024)
            events=[]
            class API:
                def __init__(self,**kw):pass
                def preupload_lfs_files(self,repo_id,additions,**kw):
                    self_op=additions[0]
                    self.assert_no_upload = all(e.get('phase')!='Upload' for e in events)
                    if not self.assert_no_upload:raise AssertionError('Hashing counted as upload')
                    with self_op.as_file(with_tqdm=True) as f:
                        self_data=f.read()
                    if len(self_data)!=1024:raise AssertionError('Bad data')
                def create_commit(self,**kw):return MagicMock(oid='abc')
            with patch.dict(os.environ,{'HF_TOKEN':'hf_test','REPO_ID':'test/repo'}),patch('huggingface_hub.HfApi',API),patch('worker.emit',lambda **kw:events.append(kw)):
                worker.upload(str(p),'folder/file')
            self.assertEqual(events[0]['phase'],'Prepare')
            self.assertTrue(any(e.get('phase')=='Upload' and e['done']==1024 for e in events))
            self.assertEqual(events[-2]['phase'],'Commit');self.assertEqual(events[-1]['result']['commit'],'abc')



class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.payload = b'actual downloaded bytes' * 4096
        async def serve(reader, writer):
            try:
                request=await reader.readuntil(b'\r\n\r\n')
                path=request.split(b' ')[1]
                if path==b'/redirect':
                    writer.write(b'HTTP/1.1 302 Found\r\nLocation: /file\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                else:
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename="actual.bin"\r\nContent-Length: '+str(len(self.payload)).encode()+b'\r\nConnection: close\r\n\r\n'+self.payload)
                await writer.drain()
            finally:writer.close();await writer.wait_closed()
        self.server=await asyncio.start_server(serve,'127.0.0.1',0)
        self.url='http://127.0.0.1:'+str(self.server.sockets[0].getsockname()[1])
    async def asyncTearDown(self):self.server.close();await self.server.wait_closed()
    async def test_real_http_stream_redirect(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'NO_PROXY':'127.0.0.1'}),patch('transfers.public_url',AsyncMock()):
            # Only this fixture bypasses the production localhost rejection.
            cfg=config(d);j=Job('x',1,1,1,'url',Path(d));p=Path(d)/'file'
            await download_http(j,self.url+'/redirect',p,cfg)
            self.assertEqual(p.read_bytes(),self.payload);self.assertEqual(j.done,len(self.payload));self.assertEqual(j.name,'actual.bin')
    async def test_file_limit_before_body(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'NO_PROXY':'127.0.0.1'}),patch('transfers.public_url',AsyncMock()):
            cfg=replace(config(d),max_bytes=10);j=Job('x',1,1,1,'url',Path(d));p=Path(d)/'file'
            with self.assertRaisesRegex(ValueError,'MAX_FILE_GB'):await download_http(j,self.url+'/file',p,cfg)
            self.assertFalse(p.exists())

class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_cancel_reaps_process(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'worker.py').write_text('import time\ntime.sleep(300)\n')
            j=Job('x',1,1,1,'url',Path(d))
            with patch('transfers.ROOT',Path(d)):
                task=asyncio.create_task(run_worker(j,'wait',cfg=config(d)))
                for _ in range(100):
                    if j.process:break
                    await asyncio.sleep(.01)
                proc=j.process;self.assertIsNotNone(proc)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertIsNotNone(proc.returncode);self.assertIsNone(j.process)

class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_uvicorn_lifecycle_dynamic_port(self):
        import socket
        import uvicorn
        import httpx
        class TestBot:
            def __init__(self,cfg):self.ok=True;self.closed=False
            async def start(self):self.loop_task=asyncio.create_task(asyncio.sleep(3600))
            def healthy(self):return self.ok
            async def close(self):
                self.closed=True;self.loop_task.cancel();await asyncio.gather(self.loop_task,return_exceptions=True)
        with tempfile.TemporaryDirectory() as d,patch('huggingface_hub.HfApi'):
            cfg=replace(config(d),port=9123)
            app=Application(cfg,TestBot)
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();sock.setblocking(False)
            port=sock.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',lifespan='on'))
            app.server=server
            task=asyncio.create_task(server.serve(sockets=[sock]))
            try:
                for _ in range(200):
                    if server.started:break
                    await asyncio.sleep(.01)
                self.assertTrue(server.started)
                async with httpx.AsyncClient(trust_env=False) as client:
                    r=await client.get(f'http://127.0.0.1:{port}/healthz');self.assertEqual(r.status_code,200)
                    app.bot.ok=False
                    r=await client.get(f'http://127.0.0.1:{port}/healthz');self.assertEqual(r.status_code,503)
            finally:
                server.should_exit=True;await asyncio.wait_for(task,5);sock.close()
            self.assertTrue(app.bot.closed);self.assertIsNone(app.lock)

if __name__=='__main__': unittest.main()
