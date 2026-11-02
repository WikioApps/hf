"""Identify the documented InstaPods app.service without guessing from credentials.

Build commands must remain finite. Only a process actually owned by app.service
may use the legacy build.sh -> start.sh compatibility path.
"""
import os
from pathlib import Path
import shutil
import subprocess


def is_app_service(proc=Path('/proc')):
    try:
        groups = (proc / 'self/cgroup').read_text().splitlines()
        if any('app.service' in line.split(':', 2)[-1].split('/') for line in groups):
            return True
    except OSError:
        pass

    # Some container configurations hide the service cgroup path. Reading the
    # unit's MainPID needs no root privileges and also handles shell wrappers.
    systemctl = shutil.which('systemctl')
    if not systemctl:
        return False
    try:
        result = subprocess.run(
            [systemctl, 'show', 'app.service', '--property=MainPID', '--value'],
            capture_output=True, text=True, timeout=2, check=False,
        )
        main_pid = int(result.stdout.strip())
        if result.returncode or main_pid <= 1:
            return False
        pid = os.getpid()
        seen = set()
        for _ in range(64):
            if pid == main_pid:
                return True
            if pid <= 1 or pid in seen:
                break
            seen.add(pid)
            # comm in /proc/PID/stat can contain spaces and parentheses.
            fields = (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            pid = int(fields[1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return False


if __name__ == '__main__':
    raise SystemExit(0 if is_app_service() else 1)
