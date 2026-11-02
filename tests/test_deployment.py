"""Regression tests for the reported systemd build-command-as-start failure."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from deployment import is_app_service

ROOT = Path(__file__).resolve().parents[1]


class ServiceDetectionTests(unittest.TestCase):
    def test_exact_cgroup_unit(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d); (proc/'self').mkdir()
            (proc/'self/cgroup').write_text('0::/system.slice/app.service\n')
            self.assertTrue(is_app_service(proc))
            (proc/'self/cgroup').write_text('0::/system.slice/instapods-agent.service\n')
            with patch('deployment.shutil.which', return_value=None):
                self.assertFalse(is_app_service(proc))
    def test_main_pid_ancestor_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d); (proc/'self').mkdir()
            (proc/'self/cgroup').write_text('0::/\n')
            (proc/'200').mkdir(); (proc/'200/stat').write_text('200 (python with spaces) S 150 0 0')
            result = subprocess.CompletedProcess([], 0, '150\n', '')
            with patch('deployment.os.getpid',return_value=200), patch('deployment.shutil.which',return_value='/usr/bin/systemctl'), patch('deployment.subprocess.run',return_value=result):
                self.assertTrue(is_app_service(proc))
                (proc/'200/stat').write_text('200 (python) S 1 0 0')
                self.assertFalse(is_app_service(proc))
    def test_unavailable_systemd_is_not_runtime(self):
        with tempfile.TemporaryDirectory() as d, patch('deployment.shutil.which',return_value='/usr/bin/systemctl'), patch('deployment.subprocess.run',side_effect=subprocess.TimeoutExpired('systemctl',2)):
            self.assertFalse(is_app_service(Path(d)))


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'.venv/bin').mkdir(parents=True)
        (self.root/'bin').mkdir()
        for name in ('build.sh','start.sh'):
            (self.root/name).write_text((ROOT/name).read_text())
        # Simulated interpreter: records build work or stays alive as a runtime.
        stub = '#!' + sys.executable + '\n' + '''import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ['deployment.py']:
    sys.exit(0 if os.environ.get('TEST_APP_SERVICE') == '1' else 1)
with open('calls.jsonl', 'a') as f:
    f.write(json.dumps({'args':args, 'pid':os.getpid(), 'path':os.environ['PATH']})+'\\n')
if args and args[0] == 'app.py':
    Path('running').write_text(str(os.getpid()))
    time.sleep(60)
'''
        for path in (self.root/'.venv/bin/python',self.root/'bin/python3'):
            path.write_text(stub);path.chmod(0o755)
        self.env = os.environ.copy()
        self.env['PATH'] = str(self.root/'bin') + os.pathsep + self.env['PATH']
    def tearDown(self): self.temp.cleanup()
    def calls(self):
        return [json.loads(line) for line in (self.root/'calls.jsonl').read_text().splitlines()]
    def assert_runtime(self, command):
        proc = subprocess.Popen(command,cwd=self.root,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline = time.monotonic()+5
            while not (self.root/'running').exists() and proc.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertIsNone(proc.poll(), 'Launcher exited instead of running the bot')
            self.assertTrue((self.root/'running').exists())
            calls = self.calls()
            self.assertEqual(len(calls),1, 'Runtime must not install dependencies or run tests')
            self.assertEqual(calls[0]['args'],['app.py'])
            self.assertEqual(int((self.root/'running').read_text()),proc.pid, 'exec must preserve the service main PID')
            self.assertTrue(calls[0]['path'].startswith(str(self.root/'.venv/bin')+os.pathsep))
        finally:
            proc.terminate();proc.communicate(timeout=5)
        self.assertEqual(proc.returncode,-signal.SIGTERM)
    def test_legacy_build_command_under_app_service_stays_running(self):
        self.env['TEST_APP_SERVICE']='1'
        self.assert_runtime(['sh','build.sh'])
    def test_start_command_stays_running(self):
        self.assert_runtime(['sh','start.sh'])
    def test_explicit_build_finishes_even_in_service(self):
        self.env['TEST_APP_SERVICE']='1'
        result=subprocess.run(['sh','build.sh','--build'],cwd=self.root,env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'running').exists())
        self.assertTrue(any('unittest' in c['args'] for c in self.calls()))
    def test_normal_build_does_not_start_even_with_credentials(self):
        self.env['BOT_TOKEN']='123:test';self.env['HF_TOKEN']='hf_test'
        result=subprocess.run(['sh','build.sh'],cwd=self.root,env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'running').exists())
    def test_missing_venv_has_useful_error(self):
        (self.root/'.venv/bin/python').unlink()
        result=subprocess.run(['sh','start.sh'],cwd=self.root,env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.assertIn('Missing .venv/bin/python',result.stderr)


if __name__ == '__main__': unittest.main()
