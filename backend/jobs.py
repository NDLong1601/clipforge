from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from . import job_store, job_events
from .errors import InputBlocked, ProviderOutcomeUnknown, ResourcePressure, job_error
from .models import uid
from . import recovery_paths
from .store import ROOT as DEFAULT_ROOT, now

# Workers only run admitted work. The dispatcher below owns resource allocation;
# queued work never occupies one of these threads while waiting for a token.
POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="clipforge-job")
ROOT = DEFAULT_ROOT
JOBS: dict[str, "Job"] = {}
LOCK = threading.RLock()
_SCHEDULER_WAKE = threading.Condition(LOCK)
_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_FUTURES: dict[str, Future] = {}
_MAINTENANCE: set[str] = set()
_HANDLERS: dict[str, Callable[["Job"], object]] = {}
_RUNTIME_HANDLERS: dict[str, Callable[["Job"], object]] = {}
_SCHEDULER_THREAD: threading.Thread | None = None
_SCHEDULER_STOP = False
_SCHEDULER_PAUSED = False
_LOW_RESOURCE_MODE = False
_GLOBAL_MAINTENANCE = False
_SHUTTING_DOWN = False
# Kinds that publish a new entry into project.exports from exports/<job id>.
EXPORT_KINDS = {"render", "portrait", "whiteboard-render"}
_RESUMABLE_KINDS = {"render", "analyze", "plan", "template", "filter-faces", "assemble-audio", "voice", "transcribe", "upload"}
_ACTIVE_RESOURCE_USE: dict[str, int] = {}
_ACTIVE_RAM_BYTES = 0
_ACTIVE_DISK_BYTES = 0
_RUNNING_PROJECTS: set[str] = set()
_RESOURCE_PRESSURE: dict[str, bool] = {}
RAM_FLOOR = 512 * 1024**2
DISK_FLOOR = 256 * 1024**2
RECOVERY_MARGIN = 128 * 1024**2

_RESOURCE_LIMITS = {
    "cpu_heavy": 2,
    "gpu_encode": 1,
    "io_heavy": 2,
    "network_ai": 2,
    "heavy_slots": 2,
    "analysis_heavy": 1,
}
_RESOURCE_PROFILES = {
    "render": ({"cpu_heavy": 1, "gpu_encode": 1, "heavy_slots": 1}, 2 * 1024**3, 1024**3),
    "portrait": ({"cpu_heavy": 1, "gpu_encode": 1, "heavy_slots": 1}, 2 * 1024**3, 1024**3),
    "whiteboard-render": ({"cpu_heavy": 1, "heavy_slots": 1}, 3 * 1024**3, 1024**3),
    "whiteboard-plan": ({}, 512 * 1024**2, 64 * 1024**2),
    "whiteboard-images": ({"network_ai": 1}, 768 * 1024**2, 512 * 1024**2),
    "analyze": ({"cpu_heavy": 1, "analysis_heavy": 1, "heavy_slots": 1}, 1024**3, 256 * 1024**2),
    "filter-faces": ({"cpu_heavy": 1, "analysis_heavy": 1, "heavy_slots": 1}, 1024**3, 256 * 1024**2),
    "transcribe": ({"cpu_heavy": 1, "analysis_heavy": 1, "network_ai": 1, "heavy_slots": 1}, 1024**3, 256 * 1024**2),
    "assemble-audio": ({"cpu_heavy": 1, "analysis_heavy": 1, "network_ai": 1, "heavy_slots": 1}, 1024**3, 512 * 1024**2),
    "voice": ({"network_ai": 1, "heavy_slots": 1}, 768 * 1024**2, 256 * 1024**2),
    "plan": ({}, 512 * 1024**2, 64 * 1024**2),
    "template": ({"analysis_heavy": 1, "cpu_heavy": 1, "heavy_slots": 1}, 1024**3, 256 * 1024**2),
    "assistant": ({"network_ai": 1}, 512 * 1024**2, 64 * 1024**2),
    "upload": ({"io_heavy": 1}, 512 * 1024**2, 128 * 1024**2),
}


def _resource_profile(kind, payload=None):
    request, ram_bytes, disk_bytes = _RESOURCE_PROFILES.get(kind, ({}, 256 * 1024**2, 0))
    request = dict(request)
    if kind in {"analyze", "plan", "template", "whiteboard-plan"} and (payload or {}).get("use_ai"):
        request["network_ai"] = 1
    return tuple(request), request, ram_bytes, disk_bytes


def _estimated_disk(kind, snapshot):
    base = _RESOURCE_PROFILES.get(kind, ({}, 0, 0))[2]
    if kind != "render" or not isinstance(snapshot, dict):
        return base
    try:
        duration = sum(
            max(0.0, float(clip.get("duration", 0)))
            for clip in snapshot.get("clips", [])
            if isinstance(clip, dict)
        )
    except (TypeError, ValueError):
        return base
    # Encoded output plus working files and safety margin; capped because this
    # remains an estimate and must not reserve an unbounded amount from JSON.
    return min(4 * 1024**3, max(base, int(512 * 1024**2 + duration * 5 * 1024**2)))


class Cancelled(Exception):
    pass


class Interrupted(Exception):
    pass


def register_handler(kind: str, handler: Callable[["Job"], object]) -> None:
    if not kind or not callable(handler):
        raise ValueError("Cần kind và handler hợp lệ")
    with LOCK:
        _HANDLERS[kind] = handler


def has_handler(kind: str) -> bool:
    return kind in _HANDLERS


class Job:
    def __init__(
        self,
        pid: str,
        kind: str,
        job_id: str | None = None,
        *,
        payload: dict | None = None,
        input_revision: int | None = None,
        input_fingerprint: str = "",
        config_fingerprint: str = "",
        snapshot_ref: str = "",
        schema_version: int = job_store.JOB_SCHEMA_VERSION,
        operation_version: int = 1,
        state_version: int = 1,
        resume_capability: str = "restart_only",
    ):
        self.id, self.pid, self.kind = job_id or uid(), pid, kind
        self.schema_version = schema_version
        self.operation_version = operation_version
        self.payload = copy.deepcopy(payload or {})
        self.input_revision = input_revision
        self.input_fingerprint = input_fingerprint
        self.config_fingerprint = config_fingerprint
        self.snapshot_ref = snapshot_ref
        self.resume_capability = resume_capability
        self.attempt = 1
        self.state_version = state_version
        self.steps: list[dict] = []
        self.blocked_reason = None
        self.error = None
        self.error_history: list[dict] = []
        self.provider_intents: list[dict] = []
        self.commit_token = ""
        self.commit_state = "uncommitted"
        self.bookkeeping_pending = False
        self.parent_job_id = None
        self.status, self.progress = "queued", 0
        self.phase, self.message = "queued", "Đang chờ xử lý"
        self.dispatch_ready = False
        self.queue_priority = 0
        self.created_at = now()
        self.queue_entered_at = self.created_at
        self.queue_entered_ts = time.time()
        self.resource_class, self.resource_request, self.estimated_ram_bytes, self.estimated_disk_bytes = _resource_profile(kind, self.payload)
        self.resource_wait_reason = ""
        self.resource_lease = False
        self.current_step = ""
        self.encode_progress = None
        self._disk_reservation = {}
        self._last_resource_check = 0.0
        self.cancelled = threading.Event()
        self.shutdown_requested = False
        self.result = None
        self.started_at = ""
        self.finished_at = ""
        self.updated_at = self.created_at
        self._state_lock = threading.RLock()
        self._commit_lock = threading.RLock()
        self._commit_done = False
        self._future: Future | None = None
        self._cleanup = None
        self._settings = None  # Effective credentials/config: process-local, never serialized.
        self._provider_calls: list[dict] = []

    @property
    def provider_calls(self):
        return self._provider_calls

    @provider_calls.setter
    def provider_calls(self, value):
        self._provider_calls = value

    @classmethod
    def from_record(cls, record: dict) -> "Job":
        legacy = not isinstance(record.get("schema_version"), int)
        job = cls(
            record["pid"],
            record["kind"],
            record["id"],
            payload=record.get("payload") if isinstance(record.get("payload"), dict) else {},
            input_revision=record.get("input_revision"),
            input_fingerprint=record.get("input_fingerprint", ""),
            config_fingerprint=record.get("config_fingerprint", ""),
            snapshot_ref=record.get("snapshot_ref", ""),
            schema_version=job_store.JOB_SCHEMA_VERSION,
            operation_version=record.get("operation_version", 1),
            state_version=record.get("state_version", 1),
            resume_capability=record.get("resume_capability", "restart_only"),
        )
        for key in (
            "status", "progress", "phase", "message", "result", "created_at",
            "started_at", "finished_at", "updated_at", "attempt", "steps",
            "blocked_reason", "error", "error_history", "commit_token", "parent_job_id",
            "queue_priority", "queue_entered_at", "queue_entered_ts",
            "resource_class", "resource_request", "estimated_ram_bytes",
            "estimated_disk_bytes", "resource_wait_reason",
            "dispatch_ready",
            "commit_state", "bookkeeping_pending",
            "current_step", "encode_progress",
        ):
            if key in record:
                setattr(job, key, record[key])
        calls = record.get("provider_calls", [])
        if isinstance(calls, list):
            job._provider_calls = [item for item in calls if isinstance(item, dict)][-100:]
        intents = record.get("provider_intents", [])
        if isinstance(intents, list):
            job.provider_intents = [item for item in intents if isinstance(item, dict)][-200:]
        if legacy:
            job.message = str(job.message or "")[:1200]
        job.resource_lease = False  # Leases are process-local and never survive restart.
        # Receipt alone is not trusted until its job/project/token are checked.
        job._commit_done = False
        return job

    def check(self):
        if self._commit_done:
            return
        if self.cancelled.is_set():
            if self.shutdown_requested:
                raise Interrupted("Tác vụ dừng theo yêu cầu đóng ứng dụng")
            raise Cancelled("Đã hủy tác vụ")
        if self.resource_lease and time.monotonic() - self._last_resource_check >= 0.5:
            self.check_resources()

    def check_resources(self):
        self._last_resource_check = time.monotonic()
        available = _available_memory_bytes()
        if available is None:
            raise ResourcePressure("TELEMETRY_UNAVAILABLE")
        if available < RAM_FLOOR:
            _RESOURCE_PRESSURE["ram"] = True
            raise ResourcePressure("MEMORY_LOW")
        volumes = _volume_state()
        required = self._disk_reservation or _disk_request(self, volumes)
        if {label for volume in volumes.values() for label in volume["labels"]} != {"data", "temp"}:
            raise ResourcePressure("TELEMETRY_UNAVAILABLE")
        for identity in required:
            volume = volumes.get(identity)
            if volume is None:
                raise ResourcePressure("TELEMETRY_UNAVAILABLE")
            if volume["free"] < DISK_FLOOR:
                _RESOURCE_PRESSURE[identity] = True
                raise ResourcePressure("DISK_LOW")

    def start_step(self, step_id):
        self.check()
        with self._state_lock:
            self.current_step = job_events.step_token(step_id)
            self.encode_progress = None
            self.updated_at = now()
            self.state_version += 1
            _persist(self)
        job_events.emit(ROOT, self, "step_started")
        self.check_resources()

    def cpu_fallback(self):
        if self.resource_lease and not self.resource_request.get("cpu_heavy"):
            raise ResourcePressure("CPU_SLOT_UNAVAILABLE")
        job_events.emit(ROOT, self, "cpu_fallback")

    def update_encode_progress(self, seconds, duration, elapsed, samples):
        eta = None
        if duration and seconds > 0 and elapsed >= 1 and samples >= 2:
            eta = round(max(0, duration - seconds) * elapsed / seconds, 1)
        with self._state_lock:
            self.encode_progress = {"processed_seconds": round(seconds, 3),
                                    "duration_seconds": duration, "eta_seconds": eta,
                                    "eta_estimated": eta is not None}
            self.updated_at = now()
            self.state_version += 1
            _persist(self)

    def update(self, progress, message, phase="processing"):
        self.check()
        with self._state_lock:
            self.progress = min(99, max(0, progress))
            self.message = str(message)
            self.phase = phase
            self.updated_at = now()
            self.state_version += 1
            _persist(self)

    def dump(self):
        with self._state_lock:
            return {
                "schema_version": self.schema_version,
                "id": self.id,
                "pid": self.pid,
                "kind": self.kind,
                "operation_version": self.operation_version,
                "input_revision": self.input_revision,
                "input_fingerprint": self.input_fingerprint,
                "config_fingerprint": self.config_fingerprint,
                "snapshot_ref": self.snapshot_ref,
                "resume_capability": self.resume_capability,
                "attempt": self.attempt,
                "state_version": self.state_version,
                "steps": copy.deepcopy(self.steps),
                "blocked_reason": self.blocked_reason,
                "error": copy.deepcopy(self.error),
                "error_history": copy.deepcopy(self.error_history),
                "provider_intents": copy.deepcopy(self.provider_intents),
                "commit_token": self.commit_token or None,
                "commit_state": self.commit_state,
                "bookkeeping_pending": self.bookkeeping_pending,
                "parent_job_id": self.parent_job_id,
                "status": self.status,
                "queue_priority": self.queue_priority,
                "queue_entered_at": self.queue_entered_at,
                "queue_entered_ts": self.queue_entered_ts,
                "resource_class": list(self.resource_class),
                "resource_request": dict(self.resource_request),
                "estimated_ram_bytes": self.estimated_ram_bytes,
                "estimated_disk_bytes": self.estimated_disk_bytes,
                "resource_wait_reason": self.resource_wait_reason,
                "current_step": self.current_step,
                "encode_progress": copy.deepcopy(self.encode_progress),
                "dispatch_ready": self.dispatch_ready,
                "progress": self.progress,
                "phase": self.phase,
                "message": self.message,
                "result": self.result,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "updated_at": self.updated_at,
                "provider_calls": copy.deepcopy(self._provider_calls),
            }

    def record_provider_call(self, record: dict):
        """Persist allowlisted routing telemetry without headers or secrets."""
        safe = {
            key: value for key, value in record.items()
            if key in {"profile_id", "name", "provider", "model", "fallback", "attempt", "elapsed_ms", "outcome", "status_code", "usage"}
        }
        with self._state_lock:
            self._provider_calls.append(safe)
            self._provider_calls = self._provider_calls[-100:]
            self.updated_at = now()
            _persist(self)

    def begin_provider_call(self, fingerprint: str, provider: str, model: str, operation: str):
        with self._state_lock:
            for intent in reversed(self.provider_intents):
                if intent.get("fingerprint") != fingerprint:
                    continue
                if intent.get("status") == "success":
                    cached = job_store.read_provider_result(ROOT, intent.get("result_ref", ""))
                    if cached is not None:
                        return intent["id"], cached
                    self.mark_provider_unknown(intent["id"], "Kết quả AI đã lưu bị thiếu; cần xác nhận gửi lại.")
                if intent.get("status") in ("sending", "outcome_unknown"):
                    self.mark_provider_unknown(intent["id"], "Kết quả yêu cầu trước chưa xác định; cần xác nhận gửi lại.")
                    raise ProviderOutcomeUnknown(
                        "Kết quả yêu cầu trước chưa xác định. Xác nhận gửi lại để tránh phát sinh phí ngoài ý muốn."
                    )
                if intent.get("status") == "retry_authorized":
                    break
            call_id = uid()
            self.provider_intents.append({
                "id": call_id,
                "fingerprint": fingerprint,
                "provider": str(provider)[:40],
                "model": str(model)[:100],
                "operation": str(operation)[:60],
                "status": "sending",
                "started_at": now(),
            })
            self.provider_intents = self.provider_intents[-200:]
            self.resume_capability = "checkpointed"
            self.state_version += 1
            self.updated_at = now()
            _persist(self)
            return call_id, None

    def complete_provider_call(self, call_id: str, result: dict):
        with self._state_lock:
            reference = job_store.write_provider_result(ROOT, self.id, call_id, result)
            intent = next((item for item in self.provider_intents if item.get("id") == call_id), None)
            if intent:
                intent["status"] = "success"
                intent["result_ref"] = reference
                intent["finished_at"] = now()
            self.state_version += 1
            self.updated_at = now()
            _persist(self)

    def finish_provider_call(self, call_id: str, outcome: str, status_code=None):
        with self._state_lock:
            intent = next((item for item in self.provider_intents if item.get("id") == call_id), None)
            if intent:
                if self.shutdown_requested and outcome == "completed_cancelled":
                    outcome = "outcome_unknown"
                intent["status"] = outcome
                intent["status_code"] = status_code
                intent["finished_at"] = now()
            self.state_version += 1
            self.updated_at = now()
            _persist(self)

    def mark_provider_unknown(self, call_id: str, message: str, *, invalid_result=False):
        with self._state_lock:
            if invalid_result:
                intent = next((item for item in self.provider_intents if item.get("id") == call_id), None)
                if intent:
                    intent["result_invalid"] = True
            self.finish_provider_call(call_id, "outcome_unknown")
            self.blocked_reason = {"code": "PROVIDER_OUTCOME_UNKNOWN", "message": message[:600]}
            _persist(self)

    def load_checkpoint(self, step_id: str, fingerprint: str):
        path = job_store.pipeline_path(ROOT, self.id)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if (manifest.get("schema_version") != 1 or manifest.get("job_id") != self.id
                    or manifest.get("input_fingerprint") != self.input_fingerprint):
                return None
            entry = manifest.get("steps", {}).get(step_id)
            if not isinstance(entry, dict) or entry.get("fingerprint") != fingerprint:
                return None
            return copy.deepcopy(entry.get("value"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def save_checkpoint(self, step_id: str, fingerprint: str, value):
        path = job_store.pipeline_path(ROOT, self.id)
        with self._state_lock:
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if (manifest.get("schema_version") != 1 or manifest.get("job_id") != self.id
                        or manifest.get("input_fingerprint") != self.input_fingerprint):
                    manifest = {}
            except (OSError, ValueError, TypeError, AttributeError):
                manifest = {}
            manifest.setdefault("schema_version", 1)
            manifest["job_id"] = self.id
            manifest["input_fingerprint"] = self.input_fingerprint
            manifest.setdefault("steps", {})[step_id] = {
                "fingerprint": fingerprint,
                "value": copy.deepcopy(value),
                "completed_at": now(),
            }
            manifest["updated_at"] = now()
            from .store import atomic

            atomic(path, manifest)
            self.resume_capability = "checkpointed"
            self.steps = [
                {"id": key, "status": "done", "fingerprint": item.get("fingerprint", ""),
                 "completed_at": item.get("completed_at", "")}
                for key, item in manifest["steps"].items()
            ]
            self.state_version += 1
            self.updated_at = now()
            _persist(self)

    def commit(self, callback, project_id: str | None = None, allow_latest=False, result_metadata=None):
        """Journal a project commit and write a receipt after its token is durable."""
        from . import store

        with self._commit_lock:
            prior = reconcile_commit(self)
            if prior["state"] == "unknown":
                raise ValueError("Chưa xác định được kết quả commit; dữ liệu phục hồi được giữ lại.")
            if prior["state"] == "committed":
                if prior.get("exception"):
                    raise prior["exception"]
                return store.read(project_id or self.pid)
            self.check()
            target_pid = project_id or self.pid
            self.start_step("commit")
            expected_revision = self.input_revision
            if allow_latest or expected_revision is None:
                expected_revision = store.read(target_pid).revision
            with self._state_lock:
                self.commit_token = self.commit_token or uid()
            intent = {
                "job_id": self.id,
                "commit_token": self.commit_token,
                "project_id": target_pid,
                "input_revision": self.input_revision,
                "expected_project_revision": expected_revision,
                "created_at": now(),
                "result_metadata": result_metadata or {},
            }
            job_store.write_commit_intent(ROOT, self.id, intent)
            with self._state_lock:
                self.state_version += 1
                _persist(self)
            try:
                with store.job_commit_token(self.commit_token, expected_revision):
                    result = callback()
            except BaseException:
                reconcile_commit(self, finalize=False)
                raise
            reconciled = reconcile_commit(self)
            if reconciled.get("exception"):
                raise reconciled["exception"]
            if reconciled["state"] != "committed":
                raise RuntimeError("Project write chưa lưu commit token")
            return result


def _persist(job: Job):
    record = job.dump()
    record["payload"] = copy.deepcopy(job.payload)
    job_store.persist(ROOT, record)


def _scheduler_settings_path():
    return ROOT / "jobs" / "scheduler.json"


def _load_scheduler_settings():
    global _SCHEDULER_PAUSED, _LOW_RESOURCE_MODE
    try:
        record = json.loads(_scheduler_settings_path().read_text(encoding="utf-8"))
        _SCHEDULER_PAUSED = bool(record.get("paused", False))
        _LOW_RESOURCE_MODE = bool(record.get("low_resource", False))
    except (OSError, ValueError, TypeError, AttributeError):
        _SCHEDULER_PAUSED = False
        _LOW_RESOURCE_MODE = False


def _save_scheduler_settings():
    from .store import atomic

    atomic(_scheduler_settings_path(), {
        "schema_version": 1,
        "paused": _SCHEDULER_PAUSED,
        "low_resource": _LOW_RESOURCE_MODE,
        "updated_at": now(),
    })


def _available_memory_bytes():
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except (ImportError, AttributeError, OSError):
        return None


def _system_cpu_percent():
    try:
        import psutil
        return float(psutil.cpu_percent(interval=None))
    except (ImportError, AttributeError, OSError):
        return None


def _effective_queue_priority(job: Job, at=None):
    at = time.time() if at is None else at
    age = max(0, int((at - float(job.queue_entered_ts or at)) // 300))
    return int(job.queue_priority) + age


def _queue_order_locked():
    at = time.time()
    waiting = [job for job in JOBS.values() if job.status == "queued" and job.dispatch_ready]
    waiting.sort(key=lambda job: (
        -_effective_queue_priority(job, at),
        float(job.queue_entered_ts or at),
        job.created_at,
        job.id,
    ))
    return waiting


def _disk_volumes():
    return {label: value["free"] for value in _volume_state().values() for label in value["labels"]}


def _volume_state():
    volumes = {}
    for label, path in (("data", ROOT), ("temp", Path(tempfile.gettempdir()))):
        try:
            path = Path(path).resolve()
            while not path.exists() and path != path.parent:
                path = path.parent
            identity = str(os.stat(path).st_dev)
            volume = volumes.setdefault(identity, {"free": int(shutil.disk_usage(path).free), "labels": []})
            volume["labels"].append(label)
        except OSError:
            continue
    return volumes


def _disk_request(job, volumes):
    # Artifacts/cache live on data. Temporary native/provider work reserves a
    # bounded extra 64 MiB on temp; aliases share one reservation ledger.
    result = {}
    for identity, volume in volumes.items():
        amount = job.estimated_disk_bytes if "data" in volume["labels"] else 0
        amount += min(job.estimated_disk_bytes, 64 * 1024**2) if "temp" in volume["labels"] else 0
        if amount:
            result[identity] = amount
    return result


def _under_pressure(key, available, required):
    threshold = required + (RECOVERY_MARGIN if _RESOURCE_PRESSURE.get(key) else 0)
    pressured = available < threshold
    _RESOURCE_PRESSURE[key] = pressured
    return pressured


def _disk_available_bytes():
    return min(_disk_volumes().values(), default=None)


def _resource_block_reason_locked(job):
    if _GLOBAL_MAINTENANCE:
        return "Đang chờ dọn dữ liệu dùng chung kết thúc."
    for resource, requested in job.resource_request.items():
        limit = _RESOURCE_LIMITS.get(resource)
        if resource == "heavy_slots" and _LOW_RESOURCE_MODE:
            limit = 1
        if limit is not None and _ACTIVE_RESOURCE_USE.get(resource, 0) + requested > limit:
            labels = {
                "cpu_heavy": "bước CPU nặng",
                "gpu_encode": "bước mã hóa",
                "io_heavy": "bước đọc/ghi dữ liệu",
                "network_ai": "yêu cầu AI",
                "heavy_slots": "số bước nặng",
                "analysis_heavy": "bước phân tích nặng",
            }
            return f"Đang chờ slot {labels.get(resource, resource)} ({_ACTIVE_RESOURCE_USE.get(resource, 0)}/{limit})."
    if job.pid in _RUNNING_PROJECTS or job.pid in _MAINTENANCE:
        return "Đang chờ tác vụ khác của cùng dự án kết thúc."
    available_ram = _available_memory_bytes()
    if available_ram is None:
        return "Không đọc được telemetry RAM; kiểm tra cài đặt psutil trước khi cấp tài nguyên."
    if _under_pressure("ram", available_ram, _ACTIVE_RAM_BYTES + job.estimated_ram_bytes + RAM_FLOOR):
        return "Đang chờ máy giải phóng RAM trước khi cấp tài nguyên."
    volumes = _volume_state()
    if {label for volume in volumes.values() for label in volume["labels"]} != {"data", "temp"}:
        return "Không đọc được telemetry dung lượng ổ data/temp; chưa thể cấp tài nguyên."
    for identity, amount in _disk_request(job, volumes).items():
        reserved = sum(active._disk_reservation.get(identity, 0) for active in JOBS.values() if active.resource_lease)
        if _under_pressure(identity, volumes[identity]["free"], reserved + amount + DISK_FLOOR):
            label = "/".join(volumes[identity]["labels"])
            return f"Đang chờ đủ dung lượng trống trên ổ {label} theo mức ước tính của tác vụ."
    return ""


def _set_wait_reason_locked(job, reason):
    if job.resource_wait_reason == reason:
        return
    with job._state_lock:
        job.resource_wait_reason = reason
        job.updated_at = now()
        job.state_version += 1
        _persist(job)


def _release_resources_locked(job):
    global _ACTIVE_RAM_BYTES, _ACTIVE_DISK_BYTES
    if not job.resource_lease:
        return
    for resource, count in job.resource_request.items():
        _ACTIVE_RESOURCE_USE[resource] = max(0, _ACTIVE_RESOURCE_USE.get(resource, 0) - count)
    _ACTIVE_RAM_BYTES = max(0, _ACTIVE_RAM_BYTES - job.estimated_ram_bytes)
    _ACTIVE_DISK_BYTES = max(0, _ACTIVE_DISK_BYTES - job.estimated_disk_bytes)
    _RUNNING_PROJECTS.discard(job.pid)
    job.resource_lease = False
    job._disk_reservation = {}
    _SCHEDULER_WAKE.notify_all()


def _job_error(job, exc):
    error = job_error(exc, job_id=job.id, step_id=job.current_step or job.kind)
    error["diagnostic_recorded"] = job_events.emit(ROOT, job,
        "resource_stopped" if isinstance(exc, ResourcePressure) else "failed",
        diagnostic_id=error["diagnostic_id"], **error["safe_details"])
    return error


def _block_inputs(job, exc):
    with job._state_lock:
        job.error = _job_error(job, exc)
        job.blocked_reason = {"code": exc.code, "message": job.error["message"]}
        job.status, job.phase = "blocked", "blocked"
        job.dispatch_ready = False
        job.message = job.error["message"]
        job.finished_at = job.updated_at = now()
        job.state_version += 1
        _persist_safely(job)


def _run_admitted_job(job: Job, handler):
    try:
        with job._state_lock:
            if job.status not in ("running", "cancelling"):
                return
        try:
            reconciliation = reconcile_commit(job)
            if reconciliation["state"] != "uncommitted":
                return
            job.check()
            from . import job_inputs
            job_inputs.validate_before_run(job, capture_settings=True)
            job.start_step(job.kind)
            result = handler(job)
            with job._state_lock:
                job.check()
                job.result = result
                job.status, job.phase = "done", "done"
                job.progress, job.message = 100, "Hoàn tất"
                job.error = None
                job.finished_at = job.updated_at = now()
                job.state_version += 1
                _persist(job)
            _clear_provider_results(job)
            job_events.emit(ROOT, job, "completed")
        except Cancelled:
            if reconcile_commit(job)["state"] != "uncommitted":
                return
            with job._state_lock:
                job.status, job.phase = "cancelled", "cancelled"
                job.message = "Đã hủy tác vụ"
                job.finished_at = job.updated_at = now()
                job.state_version += 1
                _persist_safely(job)
        except Interrupted:
            if reconcile_commit(job)["state"] != "uncommitted":
                return
            with job._state_lock:
                job.status, job.phase = "interrupted", "interrupted"
                job.message = "Tác vụ đã dừng khi ứng dụng đóng; có thể tiếp tục từ checkpoint."
                job.finished_at = job.updated_at = now()
                job.state_version += 1
                _persist_safely(job)
            _recover_provider_outcomes(job)
        except ResourcePressure as exc:
            if reconcile_commit(job)["state"] != "uncommitted":
                return
            with job._state_lock:
                job.error = _job_error(job, exc)
                job.status, job.phase = "interrupted", "resource_wait"
                job.message = job.error["message"]
                job.finished_at = job.updated_at = now()
                job.state_version += 1
                _persist_safely(job)
        except InputBlocked as exc:
            _block_inputs(job, exc)
        except ProviderOutcomeUnknown as exc:
            with job._state_lock:
                job.error = _job_error(job, exc)
                job.blocked_reason = job.blocked_reason or {
                    "code": "PROVIDER_OUTCOME_UNKNOWN", "message": str(exc)[:600]
                }
                job.status, job.phase = "blocked", "blocked"
                job.message = job.blocked_reason["message"]
                job.finished_at = job.updated_at = now()
                job.state_version += 1
                _persist_safely(job)
        except Exception as exc:
            with job._state_lock:
                job.error = _job_error(job, exc)
            reconciliation = reconcile_commit(job, finalize=False)
            with job._state_lock:
                if reconciliation["state"] == "committed":
                    job.status, job.phase = "error", "commit_pending"
                    job.bookkeeping_pending = True
                    job.message = "Kết quả đã lưu; cần thử lại để hoàn tất biên nhận/lịch sử."
                elif reconciliation["state"] != "unknown":
                    job.status, job.phase = "error", "error"
                    job.message = job.error["message"]
                job.finished_at = job.updated_at = now()
                job.state_version += 1
                _persist_safely(job)
            print(f"Job {job.id} ({job.kind}) failed: {type(exc).__name__}")
    finally:
        try:
            if job.status == "cancelled":
                with job._commit_lock:
                    if reconcile_commit(job)["state"] == "uncommitted":
                        _discard_uncommitted_job(job)
            preserve_upload_stage = job.kind == "upload" and job.status in ("error", "interrupted", "blocked")
            if job._cleanup and not preserve_upload_stage and job.commit_state != "unknown":
                try:
                    job._cleanup()
                except Exception:
                    pass
        finally:
            with LOCK:
                _FUTURES.pop(job.id, None)
                _RUNTIME_HANDLERS.pop(job.id, None)
                job._future = None
                job._settings = None
                _release_resources_locked(job)


def _dispatch_one_locked(job: Job):
    handler = _RUNTIME_HANDLERS.get(job.id) or _HANDLERS.get(job.kind)
    if handler is None:
        with job._state_lock:
            job.status, job.phase = "error", "error"
            job.error = _job_error(job, ValueError(f"Chưa đăng ký handler cho loại tác vụ {job.kind}"))
            job.message = job.error["message"]
            job.finished_at = job.updated_at = now()
            job.state_version += 1
            _persist(job)
        return
    for resource, count in job.resource_request.items():
        _ACTIVE_RESOURCE_USE[resource] = _ACTIVE_RESOURCE_USE.get(resource, 0) + count
    global _ACTIVE_RAM_BYTES, _ACTIVE_DISK_BYTES
    _ACTIVE_RAM_BYTES += job.estimated_ram_bytes
    _ACTIVE_DISK_BYTES += job.estimated_disk_bytes
    job._disk_reservation = _disk_request(job, _volume_state())
    _RUNNING_PROJECTS.add(job.pid)
    job.resource_lease = True
    try:
        with job._state_lock:
            job.status, job.phase = "running", "processing"
            job.dispatch_ready = False
            job.resource_wait_reason = ""
            job.started_at = job.updated_at = now()
            job.message = "Đang xử lý"
            job.state_version += 1
            _persist(job)
        future = POOL.submit(_run_admitted_job, job, handler)
    except Exception as exc:
        _release_resources_locked(job)
        with job._state_lock:
            job.status, job.phase = "error", "error"
            job.error = _job_error(job, exc)
            job.message = job.error["message"]
            job.finished_at = job.updated_at = now()
            job.state_version += 1
            _persist(job)
        return
    job._future = future
    _FUTURES[job.id] = future


def _scheduler_loop():
    global _SCHEDULER_STOP
    while True:
        with _SCHEDULER_WAKE:
            if _SCHEDULER_STOP:
                return
            dispatched = False
            if not _SCHEDULER_PAUSED:
                for job in _queue_order_locked():
                    try:
                        reason = _resource_block_reason_locked(job)
                    except Exception as exc:
                        print(f"Scheduler resource check failed for {job.id}: {type(exc).__name__}")
                        continue
                    if reason:
                        try:
                            _set_wait_reason_locked(job, reason)
                        except Exception as exc:
                            print(f"Scheduler could not persist wait state for {job.id}: {type(exc).__name__}")
                        continue
                    try:
                        _dispatch_one_locked(job)
                    except Exception as exc:
                        _release_resources_locked(job)
                        with job._state_lock:
                            job.status, job.phase = "error", "error"
                            job.error = _job_error(job, exc)
                            job.message = job.error["message"]
                            job.finished_at = job.updated_at = now()
                            job.state_version += 1
                            try:
                                _persist(job)
                            except Exception:
                                pass
                        print(f"Scheduler dispatch failed for {job.id}: {type(exc).__name__}")
                    dispatched = True
                    break
            if not dispatched:
                _SCHEDULER_WAKE.wait(timeout=2)


def _ensure_scheduler_locked():
    global _SCHEDULER_THREAD, _SCHEDULER_STOP
    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP = False
    _SCHEDULER_THREAD = threading.Thread(
        target=_scheduler_loop, name="clipforge-scheduler", daemon=True
    )
    _SCHEDULER_THREAD.start()


def scheduler_state():
    with LOCK:
        waiting = _queue_order_locked()
        running = [job for job in JOBS.values() if job.status in ("running", "cancelling")]
        queued_positions = {job.id: index + 1 for index, job in enumerate(waiting)}
        limits = dict(_RESOURCE_LIMITS)
        if _LOW_RESOURCE_MODE:
            limits["heavy_slots"] = 1
        active = [job for job in JOBS.values() if job.status in ("queued", "running", "cancelling")]
        recent = sorted(JOBS.values(), key=lambda job: (job.created_at, job.id))[-100:]
        included = {job.id for job in active}
        job_list = sorted(active + [job for job in recent if job.id not in included], key=lambda job: (job.created_at, job.id))
        job_records = []
        for job in job_list:
            record = job.dump()
            record["queue_position"] = queued_positions.get(job.id)
            if job.status == "queued":
                record["effective_priority"] = _effective_queue_priority(job)
            job_records.append(record)
        return {
            "paused": _SCHEDULER_PAUSED,
            "low_resource": _LOW_RESOURCE_MODE,
            "queued": sum(1 for job in JOBS.values() if job.status == "queued"),
            "running": len(running),
            "resource_use": dict(_ACTIVE_RESOURCE_USE),
            "resource_limits": limits,
            "reserved_ram_bytes": _ACTIVE_RAM_BYTES,
            "reserved_disk_bytes": _ACTIVE_DISK_BYTES,
            "available_memory_bytes": _available_memory_bytes(),
            "system_cpu_percent": _system_cpu_percent(),
            "available_disk_bytes": _disk_available_bytes(),
            "disk_volumes": _disk_volumes(),
            "reserved_disk_volumes": {label: sum(job._disk_reservation.get(identity, 0) for job in running)
                for identity, volume in _volume_state().items() for label in volume["labels"]},
            "telemetry_available": _available_memory_bytes() is not None,
            "gpu_telemetry": None,
            "jobs": job_records,
        }


def update_scheduler(*, paused=None, low_resource=None):
    global _SCHEDULER_PAUSED, _LOW_RESOURCE_MODE
    with _SCHEDULER_WAKE:
        if paused is not None:
            _SCHEDULER_PAUSED = bool(paused)
        if low_resource is not None:
            _LOW_RESOURCE_MODE = bool(low_resource)
        _save_scheduler_settings()
        _ensure_scheduler_locked()
        _SCHEDULER_WAKE.notify_all()
        return scheduler_state()


def promote(job_id):
    with _SCHEDULER_WAKE:
        job = JOBS.get(job_id)
        if not job:
            return None
        with job._state_lock:
            if job.status != "queued" or not job.dispatch_ready:
                raise ValueError("Chỉ có thể đưa job đang chờ lên đầu hàng đợi")
            other_max = max((item.queue_priority for item in JOBS.values()
                             if item.status == "queued" and item.id != job.id), default=-1)
            job.queue_priority = max(job.queue_priority, other_max + 1)
            job.resource_wait_reason = ""
            job.updated_at = now()
            job.state_version += 1
            _persist(job)
        _ensure_scheduler_locked()
        _SCHEDULER_WAKE.notify_all()
        return job.dump()


def _clear_provider_results(job: Job):
    try:
        recovery_paths.remove_tree(ROOT, ROOT / "job_runs" / job.id / "provider-results")
    except (OSError, ValueError):
        pass


def _persist_safely(job):
    try:
        _persist(job)
    except OSError as exc:
        print(f"Job {job.id}: status persist failed ({type(exc).__name__})")


def reconcile_commit(job: Job, *, finalize=True):
    """Classify durable commit evidence and finish only receipt/history/state.

    Callers must not hold LOCK: filesystem work is serialized by the job's
    commit lock, with short state-lock sections. Never infer failure from an
    absent/unreadable receipt or delete artifacts when evidence is unknown.
    """
    from . import store

    with job._commit_lock:
        intent = receipt = project = None
        receipt_valid = False
        evidence_error = None
        try:
            run = recovery_paths.checked_path(ROOT, ROOT / "job_runs" / job.id)
            intent_path = recovery_paths.checked_path(ROOT, run / "commit-intent.json")
            receipt_path = recovery_paths.checked_path(ROOT, run / "receipt.json")
            intent = job_store.read_commit_intent(ROOT, job.id)
            receipt = job_store.read_receipt(ROOT, job.id)
            def valid(record):
                return bool(isinstance(record, dict) and record.get("job_id") == job.id
                    and record.get("project_id") == job.pid
                    and re.fullmatch(r"[a-f0-9]{16}", str(record.get("commit_token", "")))
                    and (not job.commit_token or record["commit_token"] == job.commit_token))
            if intent is not None and not valid(intent) or intent is None and intent_path.exists():
                raise ValueError("Commit intent không hợp lệ hoặc không khớp tác vụ")
            if intent and (not isinstance(intent.get("result_metadata", {}), dict)
                or intent.get("expected_project_revision") is not None and not isinstance(intent["expected_project_revision"], int)):
                raise ValueError("Commit intent thiếu metadata hợp lệ")
            if receipt is not None and not valid(receipt) or receipt is None and receipt_path.exists():
                raise ValueError("Commit receipt không hợp lệ hoặc không khớp tác vụ")
            if receipt and (not isinstance(receipt.get("result"), dict)
                or receipt["result"].get("project_id") != job.pid
                or not isinstance(receipt.get("project_revision"), int)):
                raise ValueError("Commit receipt thiếu kết quả hợp lệ")
            if intent and receipt and intent["commit_token"] != receipt["commit_token"]:
                raise ValueError("Commit intent và receipt không khớp token")
            receipt_valid = receipt is not None
            if job.kind == "upload":
                from . import upload_recovery
                upload_recovery.load(ROOT, job.pid, job.id)
            token = (intent or receipt or {}).get("commit_token") or job.commit_token
            export_dir = ROOT / "projects" / job.pid / "exports" / job.id
            if token or intent or receipt or job.kind in EXPORT_KINDS and export_dir.exists():
                recovery_paths.checked_path(ROOT, store.project_dir(job.pid) / "project.json")
                with store.LOCK:
                    project = store.read(job.pid)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            evidence_error = exc
            token = (intent or receipt or {}).get("commit_token") or job.commit_token
        published = bool(project and job.kind in EXPORT_KINDS and any(item.get("id") == job.id for item in project.exports))
        if receipt_valid or project and token in project.job_commit_tokens:
            state = "committed"
        elif published:
            state = "committed"
        elif evidence_error:
            state = "unknown"
        else:
            state = "uncommitted"
        with job._state_lock:
            changed = job.commit_state != state
            job.commit_state = state
            job._commit_done = state == "committed"
            if token and state != "unknown":
                job.commit_token = token
            if state == "unknown":
                job.bookkeeping_pending = True
                job.status, job.phase = "blocked", "commit_unknown"
                job.blocked_reason = {"code": "COMMIT_OUTCOME_UNKNOWN",
                    "message": "Chưa xác định được kết quả đã lưu. Dữ liệu phục hồi được giữ; kiểm tra dự án và thử lại."}
                job.message = job.blocked_reason["message"]
                job.state_version += 1
                _persist_safely(job)
                return {"state": state, "exception": evidence_error}
            if state == "uncommitted":
                job.bookkeeping_pending = False
                if job.phase == "commit_unknown":
                    job.status, job.phase = "interrupted", "interrupted"
                    job.blocked_reason = None
                    job.message = "Đã đối chiếu: tác vụ chưa lưu kết quả; dữ liệu phục hồi được giữ."
                    changed = True
                if changed:
                    job.state_version += 1
                    _persist_safely(job)
                return {"state": state}
            was_complete = job.status == "done" and not job.bookkeeping_pending and not job.error and not job.blocked_reason
            job.bookkeeping_pending = True
        if not finalize:
            _persist_safely(job)
            return {"state": state, "pending": True}
        try:
            if token:
                store.finish_job_bookkeeping(job.pid, token)
            if receipt is None and intent:
                expected_revision = intent.get("expected_project_revision")
                revision = (expected_revision + 1
                    if token in project.job_commit_tokens and isinstance(expected_revision, int) else project.revision)
                receipt = {**intent, "project_revision": revision,
                    "result": {"project_id": job.pid, "revision": revision, **intent.get("result_metadata", {})},
                    "committed_at": now(), "recovered": True}
                job_store.write_receipt(ROOT, job.id, receipt)
            result = receipt.get("result") if receipt else {"project_id": job.pid, "revision": project.revision}
            if was_complete and job.result == result:
                with job._state_lock:
                    job.bookkeeping_pending = False
                _clear_provider_results(job)
                return {"state": state, "pending": False}
            with job._state_lock:
                if job.error:
                    job.error_history.append({"attempt": job.attempt, "error": copy.deepcopy(job.error), "finished_at": job.finished_at})
                    job.error_history = job.error_history[-50:]
                job.error = job.blocked_reason = None
                job.result = result
                job.status, job.phase = "done", "done"
                job.progress, job.message = 100, "Hoàn tất sau khi đối chiếu commit"
                job.bookkeeping_pending = False
                job.finished_at = job.finished_at or now()
                job.updated_at = now()
                job.state_version += 1
                _persist(job)
            _clear_provider_results(job)
            return {"state": state, "pending": False}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            with job._state_lock:
                job.status, job.phase = "error", "commit_pending"
                job.message = "Kết quả đã lưu; cần thử lại để hoàn tất biên nhận/lịch sử."
                job.bookkeeping_pending = True
                job.error = job.error or _job_error(job, exc)
                job.updated_at = now()
                job.state_version += 1
                _persist_safely(job)
            return {"state": state, "pending": True, "exception": exc}


def _recover_commit(job: Job) -> bool:
    return reconcile_commit(job)["state"] != "uncommitted"


def _recover_render_capability(job: Job) -> None:
    if job.kind != "render" or not job.snapshot_ref or not job.input_fingerprint:
        return
    path = ROOT / "job_runs" / job.id / "render-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest.get("job_id") == job.id
            and manifest.get("input_fingerprint") == job.input_fingerprint
            and isinstance(manifest.get("steps"), dict)
            and any(
                step_id.startswith("clip:") and entry.get("artifacts")
                for step_id, entry in manifest["steps"].items()
                if isinstance(entry, dict)
            )
        ):
            job.resume_capability = "checkpointed"
    except (OSError, ValueError, TypeError):
        return


def _recover_pipeline_capability(job: Job) -> None:
    if job.kind not in {"analyze", "plan", "template", "filter-faces", "assemble-audio", "voice", "transcribe", "upload"}:
        return
    if not job.snapshot_ref or not job.input_fingerprint:
        return
    try:
        manifest = json.loads(job_store.pipeline_path(ROOT, job.id).read_text(encoding="utf-8"))
        if (manifest.get("job_id") == job.id
                and manifest.get("input_fingerprint") == job.input_fingerprint
                and isinstance(manifest.get("steps"), dict)
                and manifest["steps"]):
            job.resume_capability = "checkpointed"
            job.steps = [
                {"id": key, "status": "done", "fingerprint": item.get("fingerprint", ""),
                 "completed_at": item.get("completed_at", "")}
                for key, item in manifest["steps"].items() if isinstance(item, dict)
            ]
    except (OSError, ValueError, TypeError, AttributeError):
        return


def _recover_provider_outcomes(job: Job) -> None:
    with job._state_lock:
        for intent in job.provider_intents:
            if intent.get("status") not in ("sending", "outcome_unknown"):
                continue
            call_id = str(intent.get("id", ""))
            reference = (
                job_store.provider_result_path(ROOT, job.id, call_id).relative_to(ROOT).as_posix()
                if re.fullmatch(r"[a-f0-9]{16}", call_id)
                else ""
            )
            cached = job_store.read_provider_result(ROOT, reference) if reference and not intent.get("result_invalid") else None
            if cached is not None:
                intent["status"] = "success"
                intent["result_ref"] = reference
                intent["finished_at"] = now()
            else:
                intent["status"] = "outcome_unknown"
                job.blocked_reason = {
                    "code": "PROVIDER_OUTCOME_UNKNOWN",
                    "provider": intent.get("provider", "provider"),
                    "model": intent.get("model", ""),
                    "operation": intent.get("operation", ""),
                    "message": "Yêu cầu có thể đã được xử lý. Gửi lại có thể phát sinh phí thêm.",
                }
                if job.status not in ("done", "cancelled"):
                    job.status, job.phase = "blocked", "blocked"
                    job.message = job.blocked_reason["message"]
                    job.finished_at = job.updated_at = now()
                    job.state_version += 1
        if not any(intent.get("status") == "outcome_unknown" for intent in job.provider_intents) and isinstance(job.blocked_reason, dict) and job.blocked_reason.get("code") == "PROVIDER_OUTCOME_UNKNOWN":
            job.blocked_reason = None
            if job.status == "blocked":
                job.status, job.phase = "interrupted", "interrupted"
        _persist_safely(job)


def _recover_snapshot_capability(job):
    if job.kind not in _RESUMABLE_KINDS or not job.snapshot_ref or not job.input_fingerprint:
        return
    if job.kind == "upload" and (not job.payload.get("files") or job.payload.get("upload_incomplete")):
        return
    try:
        from .models import Project
        recovery_paths.checked_path(ROOT, ROOT / job.snapshot_ref)
        snapshot = job_store.load_snapshot(ROOT, job.snapshot_ref)
        if Project.model_validate(snapshot).id != job.pid or job_store.fingerprint({
            "kind": job.kind, "operation_version": job.operation_version,
            "payload": job.payload, "snapshot": snapshot,
            "config_fingerprint": job.config_fingerprint,
        }) != job.input_fingerprint:
            return
        if job.resume_capability != "checkpointed":
            job.resume_capability = "snapshot"
    except (OSError, ValueError, TypeError):
        return


def initialize():
    """Exclude cleanup/admission throughout registry recovery, including disk I/O."""
    global _GLOBAL_MAINTENANCE
    with _SCHEDULER_WAKE:
        if _GLOBAL_MAINTENANCE or _MAINTENANCE:
            raise RuntimeError("Không thể khởi tạo lại khi thao tác bảo trì còn chạy")
        _GLOBAL_MAINTENANCE = True
    try:
        _initialize_jobs()
    finally:
        with _SCHEDULER_WAKE:
            _GLOBAL_MAINTENANCE = False
            _SCHEDULER_WAKE.notify_all()


def _initialize_jobs():
    """Migrate job JSON and mark work from a previous process interrupted."""
    global _ACTIVE_RAM_BYTES, _ACTIVE_DISK_BYTES, _SHUTTING_DOWN
    with _SCHEDULER_WAKE:
        if any(not future.done() for future in _FUTURES.values()):
            raise RuntimeError("Không thể khởi tạo lại khi worker cũ vẫn còn chạy")
        _SHUTTING_DOWN = False
        JOBS.clear()
        _FUTURES.clear()
        _RUNTIME_HANDLERS.clear()
        _MAINTENANCE.clear()
        _ACTIVE_RESOURCE_USE.clear()
        _ACTIVE_RAM_BYTES = 0
        _ACTIVE_DISK_BYTES = 0
        _RESOURCE_PRESSURE.clear()
        _RUNNING_PROJECTS.clear()
    _load_scheduler_settings()
    directory = ROOT / "jobs"
    if directory.exists():
        for path in directory.glob("*.json"):
            if path.name == "scheduler.json":
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or not all(record.get(key) for key in ("id", "pid", "kind")):
                    continue
                record_version = record.get("schema_version", 0)
                if isinstance(record_version, int) and not isinstance(record_version, bool) and record_version > job_store.JOB_SCHEMA_VERSION:
                    # Leave records written by a newer application untouched.
                    continue
                if not re.fullmatch(r"[a-f0-9]{16}", str(record["id"])):
                    continue
                if not re.fullmatch(r"[a-f0-9]{16}", str(record["pid"])):
                    continue
                job = Job.from_record(record)
                job.resume_capability = "restart_only"
                job.resource_class, job.resource_request, ram, disk = _resource_profile(job.kind, job.payload)
                job.estimated_ram_bytes = int(record.get("estimated_ram_bytes", ram))
                job.estimated_disk_bytes = int(record.get("estimated_disk_bytes", disk))
                if job.kind == "upload":
                    stage = ROOT / "staging" / job.pid / job.id
                    job._cleanup = lambda stage=stage: recovery_paths.remove_tree(ROOT, stage)
                with LOCK:
                    JOBS[job.id] = job
                if _recover_commit(job):
                    continue
                _recover_snapshot_capability(job)
                _recover_provider_outcomes(job)
                if job.status == "done":
                    _clear_provider_results(job)
                if job.status == "blocked":
                    continue
                if job.resume_capability == "snapshot":
                    _recover_render_capability(job)
                    _recover_pipeline_capability(job)
                if job.status in ("queued", "running", "cancelling"):
                    job.status, job.phase = "interrupted", "interrupted"
                    job.message = "Ứng dụng đã đóng khi tác vụ đang xử lý. Tiếp tục cần xác nhận."
                    job.finished_at = job.updated_at = now()
                    job.state_version += 1
                _persist(job)
            except (OSError, ValueError, TypeError, KeyError):
                continue
    with _SCHEDULER_WAKE:
        _ensure_scheduler_locked()
        _SCHEDULER_WAKE.notify_all()


def stop_render_jobs(timeout=8):
    """Return False on incomplete drain; the instance owner must stay locked."""
    global _SCHEDULER_STOP, _SHUTTING_DOWN
    deadline = time.monotonic() + timeout
    with LOCK:
        _SHUTTING_DOWN = True
        _SCHEDULER_STOP = True
        _SCHEDULER_WAKE.notify_all()
        active = [
            job for job in JOBS.values()
            if job.resource_lease or job.status in ("running", "cancelling")
        ]
        for job in active:
            job.shutdown_requested = True
            job.cancelled.set()
    scheduler_thread = _SCHEDULER_THREAD
    if scheduler_thread and scheduler_thread.is_alive() and scheduler_thread is not threading.current_thread():
        scheduler_thread.join(timeout=max(0, deadline - time.monotonic()))
    with LOCK:
        futures = list(_FUTURES.values())
    if futures:
        wait(futures, timeout=max(0, deadline - time.monotonic()))
    for job in active:
        if job.provider_intents:
            _recover_provider_outcomes(job)
    from . import processes
    with LOCK:
        drained = not any(not future.done() for future in futures) and not any(job.resource_lease for job in JOBS.values())
    return drained and not (scheduler_thread and scheduler_thread.is_alive()) and processes.active_count() == 0


def cleanup_staging():
    """Remove staging data only after jobs have been loaded and pinned."""
    staging = ROOT / "staging"
    try:
        recovery_paths.checked_path(ROOT, staging)
    except (OSError, ValueError):
        return
    if not staging.exists():
        return
    with LOCK:
        uploads = [job for job in JOBS.values() if job.kind == "upload"]
    for job in uploads:
        reconcile_commit(job)
    for project_dir in staging.iterdir():
        try:
            recovery_paths.checked_path(ROOT, project_dir)
            if not project_dir.is_dir():
                recovery_paths.unlink(ROOT, project_dir)
                continue
            for stage in project_dir.iterdir():
                # Re-read registry for each candidate: receiving jobs must be
                # pinned even before payload/files/checkpoints are persisted.
                with LOCK:
                    candidate = JOBS.get(stage.name)
                    pinned = any(job.kind == "upload" and job.pid == project_dir.name
                        and job.id == stage.name and job.status not in ("done", "cancelled")
                        for job in JOBS.values())
                if candidate is None and re.fullmatch(r"[a-f0-9]{16}", stage.name):
                    # An unreadable/newer job record is unknown ownership,
                    # not evidence that its already received bytes are garbage.
                    try:
                        record_path = recovery_paths.checked_path(ROOT, job_store.job_path(ROOT, stage.name))
                        pinned = record_path.exists()
                    except (OSError, ValueError):
                        pinned = True
                if pinned:
                    continue
                if stage.is_dir():
                    recovery_paths.remove_tree(ROOT, stage)
                else:
                    recovery_paths.unlink(ROOT, stage)
            if not any(project_dir.iterdir()):
                project_dir.rmdir()
        except (OSError, ValueError):
            # A linked/inaccessible path must never become a recursive target.
            continue


def project_lock(pid: str):
    with LOCK:
        return _PROJECT_LOCKS.setdefault(pid, threading.RLock())


def busy(pid):
    with LOCK:
        return _SHUTTING_DOWN or _GLOBAL_MAINTENANCE or pid in _MAINTENANCE or any(
            job.pid == pid and job.kind not in EXPORT_KINDS
            and job.status in ("queued", "running", "cancelling")
            for job in JOBS.values()
        )


def has_active_jobs(pid):
    with LOCK:
        return any(
            job.pid == pid and job.status in ("queued", "running", "cancelling")
            for job in JOBS.values()
        )


def has_pinned_artifacts(pid):
    with LOCK:
        candidates = [job for job in JOBS.values() if job.pid == pid]
    for job in candidates:
        if job.status in ("interrupted", "error", "blocked") or job.bookkeeping_pending:
            reconcile_commit(job)
        if job.commit_state == "unknown" or job.bookkeeping_pending or (
            job.status in ("interrupted", "error", "blocked")
            and (job.resume_capability in ("checkpointed", "snapshot") or job.kind == "upload" or job.steps
                or isinstance(job.blocked_reason, dict) and job.blocked_reason.get("code") == "PROVIDER_OUTCOME_UNKNOWN")
        ):
            return True
    return False


@contextmanager
def maintenance(pid):
    with LOCK:
        if _GLOBAL_MAINTENANCE or pid in _MAINTENANCE or any(
            job.pid == pid and job.status in ("queued", "running", "cancelling")
            for job in JOBS.values()
        ):
            raise ValueError("Dự án đang xử lý. Hãy đợi tác vụ xong trước khi sao lưu.")
        _MAINTENANCE.add(pid)
    try:
        yield
    finally:
        with LOCK:
            _MAINTENANCE.discard(pid)


@contextmanager
def cache_maintenance():
    """Exclude new admissions while reconciling and deleting outside LOCK."""
    global _GLOBAL_MAINTENANCE
    with LOCK:
        if _GLOBAL_MAINTENANCE or _MAINTENANCE or any(
            job.status in {"queued", "running", "cancelling"} or job.resource_lease
            for job in JOBS.values()
        ):
            raise ValueError("Đang có tác vụ chạy. Hãy đợi xong trước khi dọn cache.")
        _GLOBAL_MAINTENANCE = True
        pids = {job.pid for job in JOBS.values()}
    try:
        if any(has_pinned_artifacts(pid) for pid in pids):
            raise ValueError("Có tác vụ đang giữ checkpoint hoặc chờ hoàn tất commit. Hãy tiếp tục hoặc hủy tác vụ trước khi dọn cache.")
        yield
    finally:
        with _SCHEDULER_WAKE:
            _GLOBAL_MAINTENANCE = False
            _SCHEDULER_WAKE.notify_all()


def reserve(
    pid,
    kind,
    phase="queued",
    message="Đang chờ xử lý",
    cleanup=None,
    *,
    payload=None,
    snapshot=None,
    config_fingerprint="",
):
    payload = payload or {}
    # Fail at submission time if a payload cannot be persisted as JSON.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    with LOCK:
        if busy(pid):
            raise ValueError("Dự án đang xử lý. Chờ hoàn tất hoặc hủy tác vụ trước.")
        job = Job(pid, kind, payload=payload)
        job.estimated_disk_bytes = _estimated_disk(kind, snapshot)
        job.config_fingerprint = config_fingerprint
        if snapshot is not None:
            job.snapshot_ref, _ = job_store.write_snapshot(ROOT, job.id, snapshot)
            job.input_revision = snapshot.get("revision")
            job.input_fingerprint = job_store.fingerprint(
                {
                    "kind": kind,
                    "operation_version": job.operation_version,
                    "payload": payload,
                    "snapshot": snapshot,
                    "config_fingerprint": config_fingerprint,
                }
            )
            if kind in _RESUMABLE_KINDS and (kind != "upload" or payload.get("files") and not payload.get("upload_incomplete")):
                job.resume_capability = "snapshot"
            from . import job_inputs
            try:
                job_inputs.validate_revision(job)
            except BaseException:
                (ROOT / job.snapshot_ref).unlink(missing_ok=True)
                raise
        job.phase, job.message = phase, message
        job._cleanup = cleanup
        try:
            JOBS[job.id] = job
            _persist(job)
        except BaseException:
            JOBS.pop(job.id, None)
            if job.snapshot_ref:
                (ROOT / job.snapshot_ref).unlink(missing_ok=True)
            raise
        return job


def launch(job: Job, fn=None):
    """Make a persisted queued job visible to the resource-aware dispatcher."""
    with _SCHEDULER_WAKE:
        if _SHUTTING_DOWN:
            raise ValueError("Ứng dụng đang đóng; tác vụ đã lưu sẽ chờ lần mở sau")
        if job.id not in JOBS or job.status != "queued":
            raise ValueError("Tác vụ không còn ở trạng thái chờ")
        if fn is not None:
            _RUNTIME_HANDLERS[job.id] = fn
        if not (_RUNTIME_HANDLERS.get(job.id) or _HANDLERS.get(job.kind)):
            raise ValueError(f"Chưa đăng ký handler cho loại tác vụ {job.kind}")
        from . import job_inputs
        try:
            job_inputs.validate_before_run(job)
        except InputBlocked as exc:
            _block_inputs(job, exc)
            return job.dump()
        job.queue_entered_at = now()
        job.queue_entered_ts = time.time()
        job.resource_wait_reason = ""
        job._future = None
        job.dispatch_ready = True
        with job._state_lock:
            job.state_version += 1
            job.updated_at = now()
            _persist(job)
        _ensure_scheduler_locked()
        _SCHEDULER_WAKE.notify_all()
        return job.dump()


def submit(pid, kind, payload=None, snapshot=None, fn=None, config_fingerprint=""):
    """Submit a persisted payload to its registered handler.

    Passing a callable is retained for non-migrated upload code during M1.
    """
    if callable(payload) and fn is None:
        fn, payload = payload, {}
    job = reserve(
        pid, kind, payload=payload, snapshot=snapshot,
        config_fingerprint=config_fingerprint,
    )
    return launch(job, fn)


def _discard_uncommitted_job(job):
    """Caller holds this job's commit lock and has proven no publication."""
    try:
        if job.kind == "upload":
            from . import upload_recovery
            upload_recovery.restore(ROOT, job.pid, job.id, discard=True)
        if job.kind in EXPORT_KINDS:
            recovery_paths.remove_tree(ROOT, ROOT / "projects" / job.pid / "exports" / job.id)
            recovery_paths.unlink(ROOT, ROOT / "job_runs" / job.id / "render-manifest.json")
        recovery_paths.unlink(ROOT, job_store.pipeline_path(ROOT, job.id))
        _clear_provider_results(job)
        if job._cleanup:
            job._cleanup()
        with job._state_lock:
            job.resume_capability = "restart_only"
            _persist(job)
    except (OSError, ValueError) as exc:
        with job._state_lock:
            job.status, job.phase = "error", "cleanup_pending"
            job.message = "Chưa dọn được dữ liệu phục hồi; dữ liệu còn lại được giữ."
            job.error = job.error or _job_error(job, exc)
            _persist_safely(job)


def cancel(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        return None
    with job._commit_lock:
        if reconcile_commit(job)["state"] != "uncommitted":
            return job.dump()
        cleanup = False
        with LOCK, job._state_lock:
            if job.status in ("blocked", "interrupted", "error"):
                cleanup = True
                job.status, job.phase = "cancelled", "cancelled"
                job.message = "Đã bỏ tác vụ đang chờ tiếp tục"
            elif job.status == "queued" and (job._future is None or job._future.cancel()):
                cleanup = True
                job.status, job.phase = "cancelled", "cancelled"
                job.message = "Đã hủy trước khi bắt đầu"
            elif job.status in ("running", "cancelling", "queued"):
                job.status, job.phase = "cancelling", "cancelling"
                job.message = "Đang hủy. Tác vụ bên dịch vụ có thể vẫn đang xử lý."
            else:
                return job.dump()
            job.cancelled.set()
            job.finished_at = now() if cleanup else job.finished_at
            job.updated_at = now()
            job.state_version += 1
            _persist(job)  # Do not delete anything if cancellation is not durable.
            _SCHEDULER_WAKE.notify_all()
        if cleanup:
            _discard_uncommitted_job(job)
        return job.dump()


def all_jobs(pid=None, limit=100, offset=0):
    with LOCK:
        matching = [job for job in JOBS.values() if pid is None or job.pid == pid]
        matching.sort(key=lambda job: (job.created_at, job.id))
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        end = max(0, len(matching) - offset)
        start = max(0, end - limit)
        page = matching[start:end]
        active = [job for job in matching if job.status in ("queued", "running", "cancelling")]
        active_ids = {job.id for job in active}
        selected = sorted(active + [job for job in page if job.id not in active_ids], key=lambda job: (job.created_at, job.id))
        return [job.dump() for job in selected]


def get(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        return job.dump() if job else None


def resume_render(job_id, expected_state_version, config_fingerprint):
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        return None
    with job._commit_lock:
        reconciled = reconcile_commit(job)
        if reconciled["state"] == "committed":
            return job.dump()
        if reconciled["state"] == "unknown":
            raise ValueError("Chưa xác định được kết quả commit; dữ liệu phục hồi được giữ lại.")
        with job._state_lock:
            if job.status in ("queued", "running", "cancelling", "done"):
                return job.dump()
            if job.resource_lease or job._future and not job._future.done():
                raise ValueError("Worker của lần chạy trước chưa dừng; tải lại chi tiết trước khi tiếp tục.")
            if job.status not in ("interrupted", "error") or job.kind not in _RESUMABLE_KINDS:
                raise ValueError("Job này không hỗ trợ tiếp tục từ checkpoint")
            if expected_state_version != job.state_version:
                raise ValueError("Job đã đổi trạng thái. Tải lại chi tiết trước khi tiếp tục.")
            if job.resume_capability not in ("checkpointed", "snapshot") or not job.snapshot_ref:
                raise ValueError("Job này chưa có checkpoint hợp lệ; hãy tạo tác vụ mới")
        snapshot = job_store.load_snapshot(ROOT, job.snapshot_ref)
        expected_fingerprint = job_store.fingerprint(
            {
                "kind": job.kind,
                "operation_version": job.operation_version,
                "payload": job.payload,
                "snapshot": snapshot,
                "config_fingerprint": job.config_fingerprint,
            }
        )
        if expected_fingerprint != job.input_fingerprint:
            raise ValueError("Snapshot hoặc payload job không còn khớp fingerprint")
        from . import job_inputs
        try:
            if config_fingerprint != job.config_fingerprint:
                raise InputBlocked("CONFIG_CHANGED", "Cấu hình xử lý đã đổi. Tạo tác vụ từ bản hiện tại.")
            job_inputs.validate_before_run(job)
        except InputBlocked as exc:
            _block_inputs(job, exc)
            raise
        with LOCK, job._state_lock:
            if JOBS.get(job.id) is not job or busy(job.pid):
                raise ValueError("Dự án đang có tác vụ khác hoặc tác vụ đã đổi")
            if job.error:
                job.error_history.append({
                    "attempt": job.attempt,
                    "error": copy.deepcopy(job.error),
                    "finished_at": job.finished_at,
                })
                job.error_history = job.error_history[-50:]
            job.attempt += 1
            job.error = None
            job.blocked_reason = None
            job._settings = None
            job.result = None
            job.progress = 0
            job.status, job.phase = "queued", "queued"
            job.message = "Đang bắt đầu tác vụ từ đầu vào đã lưu" if job.resume_capability == "snapshot" else "Đang tiếp tục từ checkpoint đã lưu"
            job.started_at = ""
            job.finished_at = ""
            job.cancelled = threading.Event()
            job.state_version += 1
            _persist(job)
            return launch(job)


def authorize_unknown_retry(job_id, expected_state_version):
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        return None
    with job._commit_lock:
        reconciled = reconcile_commit(job)
        if reconciled["state"] == "committed":
            return job.dump()
        if reconciled["state"] == "unknown":
            raise ValueError("Chưa xác định được kết quả commit; không gửi lại yêu cầu AI.")
        with job._state_lock:
            if job.kind == "assistant":
                raise ValueError("Yêu cầu trợ lý không được lưu bền; hãy gửi lại từ giao diện trợ lý.")
            if job.resource_lease or job._future and not job._future.done():
                raise ValueError("Worker của lần chạy trước chưa dừng; chưa thể xác nhận gửi lại.")
            if job.status != "blocked" or not isinstance(job.blocked_reason, dict) or job.blocked_reason.get("code") != "PROVIDER_OUTCOME_UNKNOWN":
                raise ValueError("Job không chờ xác nhận gửi lại yêu cầu AI")
            if expected_state_version != job.state_version:
                raise ValueError("Job đã đổi trạng thái. Tải lại chi tiết trước khi xác nhận.")
            intent = next(
                (item for item in reversed(job.provider_intents) if item.get("status") == "outcome_unknown"),
                None,
            )
            if not intent:
                raise ValueError("Không tìm thấy yêu cầu AI chưa xác định")
            intent["status"] = "retry_authorized"
            intent["authorized_at"] = now()
            if job.error:
                job.error_history.append({
                    "attempt": job.attempt,
                    "error": copy.deepcopy(job.error),
                    "finished_at": job.finished_at,
                })
                job.error_history = job.error_history[-50:]
            job.error = None
            job.blocked_reason = None
            job.status, job.phase = "interrupted", "interrupted"
            job.message = "Đã xác nhận thử lại yêu cầu có kết quả chưa rõ"
            job.finished_at = job.updated_at = now()
            job.state_version += 1
            _persist(job)
            return job.dump()


def history_state(pid):
    directory = ROOT / "projects" / pid
    return {
        "can_undo": any((directory / "history").glob("*.json")),
        "can_redo": any((directory / "redo").glob("*.json")),
    }
