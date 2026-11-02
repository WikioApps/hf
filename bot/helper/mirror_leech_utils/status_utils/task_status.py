import time
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

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

    def stage(self, phase, total=0):
        logging.getLogger(__name__).info('Task phase task=%s phase=%s file=%s bytes=%s',self.id,phase,self.name,total)
        self.phase, self.total, self.done = phase, total, 0
        self.phase_start = time.monotonic()

    @property
    def speed(self):
        return self.done / max(.01, time.monotonic()-self.phase_start)

    @property
    def eta(self):
        return max(0, self.total-self.done)/self.speed if self.speed and self.total else None

    def document(self, database):
        keys = ('user_id','chat_id','message_id','destination','options','name','phase','created',
                'downloaded','results','selected','members','error')
        data = {key:getattr(self,key) for key in keys}
        data['options'] = dict(self.options)
        if self.options.get('password'):
            data['options']['password'] = database.seal(self.options['password'])
        return {'_id':self.id, 'source':database.seal(__import__('json').dumps(self.source)), **data}
