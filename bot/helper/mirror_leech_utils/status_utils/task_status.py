import time
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from collections import deque

TERMINAL = {'Complete', 'Failed', 'Cancelled', 'Interrupted'}

@dataclass
class TaskStatus:
    user_id: int
    chat_id: int
    message_id: int
    source: dict
    destination: dict
    options: dict
    directory: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = 'download.bin'
    phase: str = 'QueueDl'
    engine: str = 'Python'
    done: int = 0
    total: int = 0
    created: float = field(default_factory=time.time)
    phase_start: float = field(default_factory=time.monotonic)
    downloaded: bool = False
    results: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    members: list = field(default_factory=list)
    current: str = ''
    error: str = ''
    runner: object = None
    lanes: dict = field(default_factory=dict)
    samples: object = field(default_factory=lambda: deque(maxlen=100))
    wait_reason: str = ''
    resource_manager: object = None
    collection: bool = False
    file_count: int = 0

    @property
    def operation(self):
        mode = self.options.get('processing','none')
        if mode == 'extract':
            formats=('tar.gz','tar.bz2','tar.xz','tar.zst','zip','rar','7z','tar','tgz','tbz2','txz','gz','bz2','xz','zst','iso')
            suffix=next((suffix.upper() for suffix in formats if self.name.lower().endswith('.'+suffix)),'Archive')
            return 'Extract'+(' '+suffix if suffix else '')+' → Upload'
        if mode in ('zip','7z'):
            return mode.upper()+' → Upload'
        return ('Collection → ' if self.collection else '')+'Upload'

    def lane(self, key, name, directory=None):
        child = TaskStatus(self.user_id,self.chat_id,self.message_id,self.source,self.destination,
                           dict(self.options),directory or self.directory,id=self.id,name=name,created=self.created)
        child.resource_manager = self.resource_manager
        self.lanes[key] = child
        return child

    def snapshot(self):
        active = [c for c in self.lanes.values() if c.phase not in ('Pipeline','Complete')] or [self]
        groups = {}
        for child in active:
            group = groups.setdefault(child.phase,{'phase':child.phase,'done':0,'total':0,'speed':0,'count':0})
            group['done'] += child.done
            group['total'] += child.total
            group['speed'] += child.speed
            group['count'] += 1
        return list(groups.values())

    def stage(self, phase, total=0):
        logging.getLogger(__name__).info('Task phase task=%s phase=%s file=%s bytes=%s',self.id,phase,self.name,total)
        self.phase, self.total, self.done = phase, total, 0
        self.phase_start = time.monotonic()
        self.samples.clear()
        self.samples.append((self.phase_start,0))

    @property
    def speed(self):
        now = time.monotonic()
        if not self.samples:
            self.samples.append((self.phase_start,0))
        if now-self.samples[-1][0] >= .2:
            self.samples.append((now,self.done))
        while len(self.samples)>2 and self.samples[1][0]<now-8:
            self.samples.popleft()
        return max(0,self.done-self.samples[0][1])/max(.01,now-self.samples[0][0])

    @property
    def eta(self):
        return max(0, self.total-self.done)/self.speed if self.speed and self.total else None

    def document(self, database):
        keys = ('user_id','chat_id','message_id','destination','options','name','phase','created',
                'downloaded','results','selected','members','error','collection','file_count')
        data = {key:getattr(self,key) for key in keys}
        data['options'] = dict(self.options)
        if self.options.get('password'):
            data['options']['password'] = database.seal(self.options['password'])
        return {'_id':self.id, 'source':database.seal(__import__('json').dumps(self.source)), **data}
