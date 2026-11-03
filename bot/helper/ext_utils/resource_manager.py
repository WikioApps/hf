"""Admission control for file storage and child-process memory."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import time
import psutil

MIB = 1024**2
GIB = 1024**3


def memory_available():
    available = psutil.virtual_memory().available
    for base, limit_name, used_name in [('/sys/fs/cgroup','memory.max','memory.current'),
                                       ('/sys/fs/cgroup/memory','memory.limit_in_bytes','memory.usage_in_bytes')]:
        try:
            limit = int((Path(base)/limit_name).read_text())
            used = int((Path(base)/used_name).read_text())
            available = min(available,max(0,limit-used))
        except (OSError,ValueError):
            pass
    return available


class Reservation:
    def __init__(self, path, size):
        self.path, self.size = Path(path), size

    @property
    def remaining(self):
        try:
            written = self.path.stat().st_size
        except OSError:
            written = 0
        return max(0,self.size-written)


class ResourceManager:
    def __init__(self, directory, settings):
        self.directory, self.settings = directory, settings
        self.reservations = set()
        self.condition = asyncio.Condition()
        self.workers = 0
        self.claimed_memory = 0

    @property
    def reserve(self):
        return self.settings().get('disk_reserve_mb',1024)*MIB

    @property
    def free(self):
        return max(0,shutil.disk_usage(self.directory).free-self.reserve-
                   sum(item.remaining for item in self.reservations))

    async def pause(self, task, reason):
        task.wait_reason = reason
        if task.phase != 'Wait':
            task.stage('Wait')
        try:
            await asyncio.wait_for(self.condition.wait(),2)
        except asyncio.TimeoutError:
            pass

    async def storage(self, task, path, expected, limit, *, known=False):
        expected = max(0,int(expected or 0))
        if expected > limit:
            raise ValueError('The file exceeds the configured size limit.')
        if expected > shutil.disk_usage(self.directory).total-self.reserve:
            raise ValueError('This file cannot fit on the host with the configured disk reserve.')
        start = time.monotonic()
        async with self.condition:
            while True:
                existing = Path(path).stat().st_size if Path(path).is_file() else 0
                # Unknown-length streams receive a fixed budget; they cannot consume other jobs’ reservations.
                budget = expected if expected or known else min(limit,max(0,self.free+existing))
                needed = max(0,budget-existing)
                if (known or expected or budget >= min(limit,64*MIB)) and needed <= self.free and memory_available() >= 96*MIB:
                    lease = Reservation(path,budget)
                    self.reservations.add(lease)
                    task.wait_reason = ''
                    return lease
                if time.monotonic()-start > 900:
                    raise ValueError('Storage or memory stayed unavailable for 15 minutes. Clean retained files or lower concurrency, then retry.')
                await self.pause(task,'Waiting for disk space or memory; uploads continue.')

    async def release(self, lease):
        async with self.condition:
            self.reservations.discard(lease)
            self.condition.notify_all()

    @asynccontextmanager
    async def worker(self, task):
        start = time.monotonic()
        previous=(task.phase,task.total)
        async with self.condition:
            while memory_available()-self.claimed_memory-128*MIB < 192*MIB:
                if time.monotonic()-start > 900:
                    raise ValueError('Not enough memory to start a worker. Lower concurrency and retry.')
                await self.pause(task,'Waiting for memory; other transfers are continuing.')
            budget=min(512*MIB,memory_available()-self.claimed_memory-128*MIB)
            self.workers += 1
            self.claimed_memory += budget
        task.wait_reason = ''
        if task.phase=='Wait':task.stage(*previous)
        try:
            yield budget
        finally:
            async with self.condition:
                self.workers -= 1
                self.claimed_memory -= budget
                self.condition.notify_all()
