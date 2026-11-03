"""Admission control for file storage and child-process memory."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import time
import psutil

MIB = 1024**2
GIB = 1024**3
STREAM_WINDOW = 32*MIB


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


def allocated(path):
    try:
        stat = Path(path).stat()
        return min(stat.st_size, getattr(stat,'st_blocks',(stat.st_size+511)//512)*512)
    except OSError:
        return 0


class Reservation:
    def __init__(self, path, size, dynamic=False):
        self.path, self.size, self.dynamic = Path(path), size, dynamic

    @property
    def remaining(self):
        # Sparse aria2 files can have their final length before the blocks exist.
        if self.dynamic:
            return min(STREAM_WINDOW,max(0,self.size-allocated(self.path)))
        return max(0,self.size-allocated(self.path))


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
    def reserved(self):
        return sum(item.remaining for item in self.reservations)

    @property
    def available(self):
        return shutil.disk_usage(self.directory).free-self.reserve-self.reserved

    @property
    def free(self):
        return max(0,self.available)

    def check(self, lease):
        if lease in self.reservations and self.available < 0:
            raise ValueError('Download stopped before disk space ran out. Other transfers have reserved the remaining space. Retry after they finish.')

    async def pause(self, task, reason):
        task.wait_reason = reason
        if task.phase != 'Wait':
            task.stage('Wait')
        try:
            await asyncio.wait_for(self.condition.wait(),1)
        except asyncio.TimeoutError:
            pass

    async def storage(self, task, path, expected, limit, *, known=False):
        expected = max(0,int(expected or 0))
        if expected > limit:
            raise ValueError('The file exceeds the configured size limit.')
        if expected > shutil.disk_usage(self.directory).total-self.reserve:
            raise ValueError('This file cannot fit on the host with the configured disk reserve.')
        dynamic = not (known or expected)
        lease = Reservation(path,limit if dynamic else expected,dynamic)
        start = time.monotonic()
        async with self.condition:
            while lease.remaining > self.free or memory_available() < 96*MIB:
                if time.monotonic()-start > 900:
                    raise ValueError('Storage or memory stayed unavailable for 15 minutes. Wait for active transfers or increase host capacity.')
                await self.pause(task,'Waiting for available disk or RAM; active uploads continue.')
            self.reservations.add(lease)
            task.wait_reason = ''
            return lease

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
