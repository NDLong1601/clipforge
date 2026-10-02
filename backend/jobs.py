from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .models import uid
from .store import ROOT as DEFAULT_ROOT, atomic, now

POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="clipforge-job")
ROOT = DEFAULT_ROOT
JOBS: dict[str, "Job"] = {}
LOCK = threading.RLock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_FUTURES: dict[str, Future] = {}
_MAINTENANCE: set[str] = set()


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, pid: str, kind: str, job_id: str | None = None):
        self.id, self.pid, self.kind = job_id or uid(), pid, kind
        self.status, self.progress = "queued", 0
        self.phase, self.message = "queued", "Đang chờ xử lý"
        self.cancelled = threading.Event()
        self.result = None
        self.created_at = now()
        self.started_at = ""
        self.finished_at = ""
        self.updated_at = self.created_at
        self._state_lock = threading.RLock()
        self._commit_done = False
        self._future: Future | None = None
        self._cleanup = None
        self.provider_calls: list[dict] = []

    @classmethod
    def from_record(cls, record: dict) -> "Job":
        job = cls(record["pid"], record["kind"], record["id"])
        for key in (
            "status",
            "progress",
            "phase",
            "message",
            "result",
            "created_at",
            "started_at",
            "finished_at",
            "updated_at",
        ):
            if key in record:
                setattr(job, key, record[key])
        calls = record.get("provider_calls", [])
        if isinstance(calls, list):
            job.provider_calls = [item for item in calls if isinstance(item, dict)][
                -100:
            ]
        return job

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled("Đã hủy tác vụ")

    def update(self, progress, message, phase="processing"):
        self.check()
        with self._state_lock:
            self.progress = min(99, max(0, progress))
            self.message = str(message)
            self.phase = phase
            self.updated_at = now()
            _persist(self)

    def dump(self):
        with self._state_lock:
            return {
                key: getattr(self, key)
                for key in (
                    "id",
                    "pid",
                    "kind",
                    "status",
                    "progress",
                    "phase",
                    "message",
                    "result",
                    "created_at",
                    "started_at",
                    "finished_at",
                    "updated_at",
                    "provider_calls",
                )
            }

    def record_provider_call(self, record: dict):
        """Persist safe routing telemetry without request headers or secrets."""
        safe = {
            key: value
            for key, value in record.items()
            if key
            in {
                "profile_id",
                "name",
                "provider",
                "model",
                "fallback",
                "attempt",
                "elapsed_ms",
                "outcome",
                "status_code",
                "usage",
            }
        }
        with self._state_lock:
            self.provider_calls.append(safe)
            self.provider_calls = self.provider_calls[-100:]
            self.updated_at = now()
            _persist(self)

    def commit(self, callback):
        """Make the final project write indivisible with respect to cancellation."""
        with self._state_lock:
            self.check()
            result = callback()
            self._commit_done = True
            return result


def _persist(job: Job):
    atomic(ROOT / "jobs" / f"{job.id}.json", job.dump())


def initialize():
    """Reload persisted jobs and mark work from a previous process as interrupted."""
    with LOCK:
        JOBS.clear()
        _FUTURES.clear()
        _MAINTENANCE.clear()
        directory = ROOT / "jobs"
        if not directory.exists():
            return
        for path in directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or not all(
                    record.get(key) for key in ("id", "pid", "kind")
                ):
                    continue
                job = Job.from_record(record)
                if job.status in ("queued", "running", "cancelling"):
                    job.status = "interrupted"
                    job.phase = "interrupted"
                    job.message = "Ứng dụng đã đóng khi tác vụ đang xử lý. Bạn có thể chủ động chạy lại."
                    job.finished_at = job.updated_at = now()
                    _persist(job)
                JOBS[job.id] = job
            except (OSError, ValueError, TypeError, KeyError):
                continue


def cleanup_staging():
    staging = ROOT / "staging"
    if staging.exists():
        for path in staging.iterdir():
            if path.is_dir():
                import shutil

                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def project_lock(pid: str):
    with LOCK:
        return _PROJECT_LOCKS.setdefault(pid, threading.RLock())


def busy(pid):
    with LOCK:
        return pid in _MAINTENANCE or any(
            j.pid == pid and j.status in ("queued", "running", "cancelling")
            for j in JOBS.values()
        )


@contextmanager
def maintenance(pid):
    """Reserve one project for synchronous maintenance without holding global locks.

    API writers and job reservations already check busy() under LOCK, so they
    reject changes to this project while other projects remain responsive.
    """
    with LOCK:
        if busy(pid):
            raise ValueError("Dự án đang xử lý. Hãy đợi tác vụ xong trước khi sao lưu.")
        _MAINTENANCE.add(pid)
    try:
        yield
    finally:
        with LOCK:
            _MAINTENANCE.discard(pid)


def reserve(pid, kind, phase="queued", message="Đang chờ xử lý", cleanup=None):
    with LOCK:
        if busy(pid):
            raise ValueError("Dự án đang xử lý. Chờ hoàn tất hoặc hủy tác vụ trước.")
        job = Job(pid, kind)
        job.phase, job.message = phase, message
        job._cleanup = cleanup
        JOBS[job.id] = job
        try:
            _persist(job)
        except BaseException:
            JOBS.pop(job.id, None)
            raise
        return job


def launch(job: Job, fn):
    with LOCK:
        if job.id not in JOBS or job.status != "queued":
            raise ValueError("Tác vụ không còn ở trạng thái chờ")

        def run():
            with job._state_lock:
                if job.status == "cancelling" or job.cancelled.is_set():
                    job.status, job.phase = "cancelled", "cancelled"
                    job.message = "Đã hủy trước khi bắt đầu"
                    job.finished_at = job.updated_at = now()
                    _persist(job)
                    if job._cleanup:
                        job._cleanup()
                    return
                if job.status != "queued":
                    return
                job.status, job.phase = "running", "processing"
                job.started_at = job.updated_at = now()
                job.message = "Đang xử lý"
                _persist(job)
            try:
                job.check()
                result = fn(job)
                with job._state_lock:
                    job.check()
                    job.result = result
                    job.status, job.phase = "done", "done"
                    job.progress, job.message = 100, "Hoàn tất"
                    job.finished_at = job.updated_at = now()
                    _persist(job)
            except Cancelled:
                with job._state_lock:
                    job.status, job.phase = "cancelled", "cancelled"
                    job.message = "Đã hủy tác vụ"
                    job.finished_at = job.updated_at = now()
                    _persist(job)
            except Exception as exc:
                with job._state_lock:
                    job.status, job.phase = "error", "error"
                    job.message = str(exc)[:1200]
                    job.finished_at = job.updated_at = now()
                    _persist(job)
                # Never print provider request headers or response bodies.
                print(f"Job {job.id} ({job.kind}) failed: {type(exc).__name__}")
            finally:
                if job._cleanup:
                    try:
                        job._cleanup()
                    except OSError:
                        pass

        future = POOL.submit(run)
        job._future = future
        _FUTURES[job.id] = future
        return job.dump()


def submit(pid, kind, fn):
    job = reserve(pid, kind)
    return launch(job, fn)


def cancel(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        with job._state_lock:
            if job.status not in ("queued", "running", "cancelling"):
                return job.dump()
            if job._commit_done:
                return job.dump()
            job.cancelled.set()
            if job.status == "queued":
                if job._future is None or job._future.cancel():
                    job.status, job.phase = "cancelled", "cancelled"
                    job.message = "Đã hủy trước khi bắt đầu"
                    job.finished_at = job.updated_at = now()
                    _persist(job)
                    if job._cleanup:
                        job._cleanup()
                else:
                    job.status, job.phase = "cancelling", "cancelling"
                    job.message = "Đang hủy trước khi bắt đầu"
                    job.updated_at = now()
                    _persist(job)
            elif job.status == "running":
                job.status, job.phase = "cancelling", "cancelling"
                job.message = "Đang hủy. Tác vụ bên dịch vụ có thể vẫn đang xử lý."
                job.updated_at = now()
                _persist(job)
            return job.dump()


def all_jobs(pid=None):
    with LOCK:
        matching = [j for j in JOBS.values() if pid is None or j.pid == pid]
        matching.sort(key=lambda job: (job.created_at, job.id))
        return [job.dump() for job in matching[-100:]]


def history_state(pid):
    directory = ROOT / "projects" / pid
    return {
        "can_undo": any((directory / "history").glob("*.json")),
        "can_redo": any((directory / "redo").glob("*.json")),
    }
