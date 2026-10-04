"""Own the scheduler and every submitted worker for one isolated test."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
import threading

from backend import jobs, store


class TestJobPool(ThreadPoolExecutor):
    __test__ = False

    def __init__(self):
        super().__init__(max_workers=6, thread_name_prefix="clipforge-test-job")
        self.submissions = []

    def submit(self, fn, /, *args, **kwargs):
        # Retain the job even when a test/initialize() clears JOBS or _FUTURES.
        future = super().submit(fn, *args, **kwargs)
        self.submissions.append((future, args[0] if args and isinstance(args[0], jobs.Job) else None))
        return future


def stop_and_join(pool, timeout=15, *, close_pool=True):
    """Stop dispatch first; wait for workers while their original ROOT is valid."""
    with jobs._SCHEDULER_WAKE:
        jobs._SCHEDULER_STOP = True
        jobs._SCHEDULER_WAKE.notify_all()
        scheduler = jobs._SCHEDULER_THREAD
        owned_jobs = list(jobs.JOBS.values())
        if isinstance(pool, TestJobPool):
            owned_jobs += [job for _, job in pool.submissions if job is not None]
        for job in owned_jobs:
            job.shutdown_requested = True
            job.cancelled.set()
    if scheduler and scheduler is not threading.current_thread():
        scheduler.join(timeout=timeout)
        if scheduler.is_alive():
            raise RuntimeError("Test scheduler did not stop; refusing to switch data roots")
    futures = [future for future, _ in pool.submissions] if isinstance(pool, TestJobPool) else list(jobs._FUTURES.values())
    _, pending = wait(futures, timeout=timeout)
    if pending:
        raise RuntimeError("Test workers did not stop; refusing to switch data roots")
    if isinstance(pool, TestJobPool) and close_pool:
        pool.shutdown(wait=True, cancel_futures=True)
        if any(thread.is_alive() for thread in pool._threads):
            raise RuntimeError("Test pool has a live thread after shutdown")


@contextmanager
def isolated_job_runtime(root):
    """Restore identities and contents of all scheduler globals after joining."""
    old_pool = jobs.POOL
    mappings = (
        "JOBS", "_PROJECT_LOCKS", "_FUTURES", "_HANDLERS", "_RUNTIME_HANDLERS",
        "_ACTIVE_RESOURCE_USE", "_RESOURCE_LIMITS", "_RESOURCE_PROFILES", "_RESOURCE_PRESSURE",
    )
    sets = ("_MAINTENANCE", "_RUNNING_PROJECTS")
    scalars = (
        "ROOT", "POOL", "_SCHEDULER_THREAD", "_SCHEDULER_STOP", "_SCHEDULER_PAUSED",
        "_LOW_RESOURCE_MODE", "_ACTIVE_RAM_BYTES", "_ACTIVE_DISK_BYTES",
        "_GLOBAL_MAINTENANCE",
        "_SHUTTING_DOWN",
    )
    saved = {name: getattr(jobs, name) for name in mappings + sets + scalars}
    stop_and_join(old_pool, close_pool=False)
    # An existing scheduler was joined, so its old thread cannot be restored.
    saved["_SCHEDULER_THREAD"] = None
    old_root = store.ROOT
    pool = TestJobPool()
    for name in mappings:
        setattr(jobs, name, dict(saved[name]) if name in {"_HANDLERS", "_RESOURCE_LIMITS", "_RESOURCE_PROFILES"} else {})
    for name in sets:
        setattr(jobs, name, set())
    root.mkdir(parents=True, exist_ok=True)
    store.ROOT = jobs.ROOT = root
    jobs.POOL = pool
    jobs._SCHEDULER_THREAD = None
    jobs._SCHEDULER_STOP = False
    jobs._SCHEDULER_PAUSED = False
    jobs._LOW_RESOURCE_MODE = False
    jobs._GLOBAL_MAINTENANCE = False
    jobs._SHUTTING_DOWN = False
    jobs._ACTIVE_RAM_BYTES = jobs._ACTIVE_DISK_BYTES = 0
    try:
        yield pool
    finally:
        # Deliberately do not restore state if join fails: a live writer must
        # never be given another test's data directory.
        stop_and_join(pool)
        for name, value in saved.items():
            setattr(jobs, name, value)
        store.ROOT = old_root
