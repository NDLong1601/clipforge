"""Bounded local event journal. Only enums, generated IDs and numbers enter it."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .store import now

MAX_BYTES = 64 * 1024
GENERATIONS = 3
_LOCK = threading.RLock()
_EVENTS = {"step_started", "failed", "resource_stopped", "cpu_fallback", "completed"}
_STEPS = {"render", "analyze", "plan", "template", "filter-faces", "assemble-audio",
          "voice", "transcribe", "upload", "assistant", "ffmpeg", "preflight", "commit",
          "join-video", "source-audio", "subtitles", "mix-audio", "encode-video", "mux"}


def step_token(value):
    value = str(value or "")
    return value if value in _STEPS or re.fullmatch(r"clip:\d{3,6}|proxy:[a-f0-9]{8,64}", value) else "unknown"


def _path(root, job_id):
    if not re.fullmatch(r"[a-f0-9]{8,64}", str(job_id)):
        return None
    return Path(root) / "job_runs" / job_id / "events.jsonl"


def safe_event(value):
    if not isinstance(value, dict) or value.get("event") not in _EVENTS:
        return None
    result = {"event": value["event"], "step_id": step_token(value.get("step_id"))}
    for key in ("attempt", "progress", "available_bytes", "http_status"):
        number = value.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and 0 <= number < 10**16:
            result[key] = number
    for key, pattern in (("diagnostic_id", r"[a-f0-9]{8,64}"),
                         ("occurred_at", r"\d{4}-\d\d-\d\d[T ][0-9:.+Z-]{8,32}")):
        token = value.get(key)
        if isinstance(token, str) and re.fullmatch(pattern, token):
            result[key] = token
    return result


def emit(root, job, event, **fields):
    path = _path(root, job.id)
    record = safe_event({"event": event, "step_id": job.current_step,
                         "attempt": job.attempt, "progress": job.progress,
                         "occurred_at": now(), **fields})
    if path is None or record is None:
        return False
    try:
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(line) > MAX_BYTES:
                path.with_suffix(f".jsonl.{GENERATIONS - 1}").unlink(missing_ok=True)
                for index in range(GENERATIONS - 2, 0, -1):
                    source = path.with_suffix(f".jsonl.{index}")
                    if source.exists():
                        source.replace(path.with_suffix(f".jsonl.{index + 1}"))
                path.replace(path.with_suffix(".jsonl.1"))
            with path.open("ab") as stream:
                stream.write(line)
        return True
    except OSError:
        return False  # A full disk must not hide the worker error or retain its lease.


def read(root, job_id):
    path = _path(root, job_id)
    if path is None:
        return []
    result = []
    with _LOCK:
        for source in [*(path.with_suffix(f".jsonl.{i}") for i in range(GENERATIONS - 1, 0, -1)), path]:
            try:
                for line in source.read_bytes()[:MAX_BYTES].splitlines():
                    try:
                        record = safe_event(json.loads(line))
                        if record:
                            result.append(record)
                    except (ValueError, TypeError):
                        continue
            except OSError:
                continue
    return result[-1000:]
