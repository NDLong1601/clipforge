"""Exercise teardown with a real scheduler/worker, not just empty dictionaries."""

import threading

from backend import jobs, store
from tests.fixtures.job_runtime import isolated_job_runtime


def test_runtime_joins_live_worker_and_scheduler_before_restoring_root(tmp_path):
    old_jobs, old_pool, old_root = jobs.JOBS, jobs.POOL, store.ROOT
    started = threading.Event()
    stopped = threading.Event()
    root = tmp_path / "nested-runtime"

    def worker(job):
        started.set()
        job.cancelled.wait(timeout=5)
        (store.ROOT / "worker-last-write.txt").write_text("owned", encoding="utf-8")
        stopped.set()
        job.check()

    with isolated_job_runtime(root) as pool:
        job = jobs.reserve("a" * 16, "fixture")
        jobs.launch(job, worker)
        assert started.wait(timeout=5)
        scheduler = jobs._SCHEDULER_THREAD
        # Emulate old fixtures/initialize() dropping all job references.
        jobs.JOBS.clear()
        jobs._FUTURES.clear()

    assert stopped.is_set()
    assert (root / "worker-last-write.txt").read_text(encoding="utf-8") == "owned"
    assert not scheduler.is_alive()
    assert all(future.done() for future, _ in pool.submissions)
    assert not any(thread.is_alive() for thread in pool._threads)
    assert jobs.JOBS is old_jobs
    assert jobs.POOL is old_pool
    assert store.ROOT == old_root
