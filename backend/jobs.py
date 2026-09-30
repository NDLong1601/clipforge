import threading, traceback
from concurrent.futures import ThreadPoolExecutor
from .models import uid
from .store import ROOT, atomic, now

POOL = ThreadPoolExecutor(max_workers=2)
JOBS = {}
LOCK = threading.RLock()

class Cancelled(Exception):
    pass

class Job:
    def __init__(self, pid, kind):
        self.id, self.pid, self.kind = uid(), pid, kind
        self.status, self.progress, self.message = 'queued', 0, 'Đang chờ xử lý'
        self.cancelled = threading.Event()
        self.result = None
        self.created_at = now()
    def check(self):
        if self.cancelled.is_set():
            raise Cancelled('Đã hủy tác vụ')
    def update(self, progress, message):
        self.check()
        self.progress, self.message = min(99, max(0, progress)), message
    def dump(self):
        return {k:getattr(self,k) for k in ['id','pid','kind','status','progress','message','result','created_at']}

def busy(pid):
    return any(j.pid == pid and j.status in ['queued','running'] for j in JOBS.values())

def submit(pid, kind, fn):
    with LOCK:
        if busy(pid):
            raise ValueError('Dự án đang xử lý. Chờ hoàn tất hoặc hủy tác vụ trước.')
        j = Job(pid, kind)
        JOBS[j.id] = j
    def run():
        j.status = 'running'
        try:
            j.result = fn(j)
            j.check()
            j.status, j.progress, j.message = 'done', 100, 'Hoàn tất'
        except Cancelled:
            j.status, j.message = 'cancelled', 'Đã hủy tác vụ'
        except Exception as exc:
            j.status, j.message = 'error', str(exc)[:1200]
            # Tracebacks contain source locations only; never log provider request headers.
            print(f'Job {j.id} ({kind}) failed: {type(exc).__name__}')
        atomic(ROOT / 'jobs' / f'{j.id}.json', j.dump())
    POOL.submit(run)
    return j.dump()

def all_jobs(pid=None):
    return [j.dump() for j in JOBS.values() if pid is None or j.pid == pid][-30:]
