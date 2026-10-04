"""Privacy-filtered, user-initiated diagnostics exports for local jobs."""

from __future__ import annotations

import io
import json
import platform
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from .errors import safe_message


APP_VERSION = "1.0.0"
DIAGNOSTIC_FILES = [
    {
        "name": "report.json",
        "description": "Phiên bản ứng dụng, hệ điều hành, Python/FFmpeg, trạng thái job và telemetry tài nguyên đã lọc.",
    },
    {
        "name": "README.txt",
        "description": "Phạm vi dữ liệu, những nội dung được loại trừ và cách chia sẻ thủ công.",
    },
]


def _safe_token(value, pattern=r"[A-Za-z0-9_.:-]{1,80}"):
    value = str(value or "")
    return value if re.fullmatch(pattern, value) else "unknown"


def _ffmpeg_version():
    try:
        from .media import ffmpeg

        executable = ffmpeg()
        output = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, timeout=3,
            check=False,
        ).stdout
        first_line = output.splitlines()[0] if output else ""
        match = re.search(r"ffmpeg version\s+([^\s]+)", first_line, flags=re.I)
        return match.group(1)[:80] if match else "unavailable"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unavailable"


def preview_manifest():
    return {
        "files": DIAGNOSTIC_FILES,
        "excludes": [
            "API keys, authorization headers, cookies and service URL parameters",
            "prompts, scripts, transcripts, audio, video, thumbnails and project content",
            "project names, project paths, personal file paths and raw application logs",
        ],
        "logs_included": False,
        "safe_events_included": True,
    }


def _safe_error(error):
    if not isinstance(error, dict):
        return None
    safe = {}
    for key in ("code", "category", "severity", "retryable", "occurred_at", "diagnostic_id", "diagnostic_recorded"):
        value = error.get(key)
        if isinstance(value, (str, bool, int)) or value is None:
            safe[key] = value
    from .job_events import step_token
    safe["step_id"] = step_token(error.get("step_id"))
    details = error.get("safe_details")
    if isinstance(details, dict):
        safe["safe_details"] = {
            "exception_type": _safe_token(details.get("exception_type")),
            "http_status": details.get("http_status") if isinstance(details.get("http_status"), int) else None,
        }
    return safe


def _provider_calls(job):
    result = []
    for call in (getattr(job, "provider_calls", []) or [])[-50:]:
        if not isinstance(call, dict):
            continue
        result.append({
            "provider": _safe_token(call.get("provider")),
            "outcome": _safe_token(call.get("outcome")),
            "attempt": call.get("attempt") if isinstance(call.get("attempt"), int) else None,
            "status_code": call.get("status_code") if isinstance(call.get("status_code"), int) else None,
            "elapsed_ms": call.get("elapsed_ms") if isinstance(call.get("elapsed_ms"), int) else None,
            "fallback": call.get("fallback") if isinstance(call.get("fallback"), bool) else None,
        })
    return result


def _resource_snapshot(scheduler):
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    numeric_fields = (
        "available_memory_bytes", "available_disk_bytes", "system_cpu_percent",
        "reserved_ram_bytes", "reserved_disk_bytes", "queued", "running",
    )
    snapshot = {key: scheduler.get(key) for key in numeric_fields if isinstance(scheduler.get(key), (int, float))}
    for key in ("resource_use", "resource_limits"):
        value = scheduler.get(key)
        if isinstance(value, dict):
            snapshot[key] = {
                _safe_token(name): count for name, count in value.items()
                if isinstance(count, (int, float)) and not isinstance(count, bool)
            }
    for key in ("paused", "low_resource"):
        if isinstance(scheduler.get(key), bool):
            snapshot[key] = scheduler[key]
    return snapshot


def build_report(job, scheduler=None):
    """Build a strict allowlist; never serialize Job.dump() or application settings."""
    try:
        from . import render

        encoder = render._VIDEO_ENCODER
        encoder_name = _safe_token(encoder[0]) if encoder else "not-probed"
    except Exception:
        encoder_name = "unavailable"

    history = []
    for attempt in (getattr(job, "error_history", []) or [])[-50:]:
        if isinstance(attempt, dict):
            history.append({
                "attempt": attempt.get("attempt") if isinstance(attempt.get("attempt"), int) else None,
                "finished_at": attempt.get("finished_at") if isinstance(attempt.get("finished_at"), str) else None,
                "error": _safe_error(attempt.get("error")),
            })

    steps = []
    for step in (getattr(job, "steps", []) or [])[:100]:
        if isinstance(step, dict):
            steps.append({
                "id": _safe_token(step.get("id")),
                "status": _safe_token(step.get("status")),
                "completed_at": step.get("completed_at") if isinstance(step.get("completed_at"), str) else None,
            })

    from . import jobs, job_events
    events = job_events.read(jobs.ROOT, getattr(job, "id", ""))
    return {
        "schema_version": 1,
        "application": {"name": "ClipForge", "version": APP_VERSION},
        "runtime": {
            "os": platform.system(),
            "os_release": platform.release(),
            "python": ".".join(map(str, sys.version_info[:3])),
            "ffmpeg": _ffmpeg_version(),
            "video_encoder": encoder_name,
        },
        "job": {
            "kind": _safe_token(getattr(job, "kind", "")),
            "status": _safe_token(getattr(job, "status", "")),
            "phase": _safe_token(getattr(job, "phase", "")),
            "current_step": job_events.step_token(getattr(job, "current_step", "")),
            "attempt": getattr(job, "attempt", None),
            "state_version": getattr(job, "state_version", None),
            "progress": getattr(job, "progress", None),
            "created_at": getattr(job, "created_at", None),
            "started_at": getattr(job, "started_at", None),
            "updated_at": getattr(job, "updated_at", None),
            "finished_at": getattr(job, "finished_at", None),
            "resource_class": [
                _safe_token(item) for item in (getattr(job, "resource_class", []) or [])[:20]
            ],
            "resource_request": {
                _safe_token(key): value for key, value in (getattr(job, "resource_request", {}) or {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
            "estimated_ram_bytes": getattr(job, "estimated_ram_bytes", None),
            "estimated_disk_bytes": getattr(job, "estimated_disk_bytes", None),
            "error": _safe_error(getattr(job, "error", None)),
            "error_history": history,
            "steps": steps,
            "provider_calls": _provider_calls(job),
        },
        "resources": _resource_snapshot(scheduler),
        "events": events,
        "event_retention": {"max_file_bytes": job_events.MAX_BYTES, "generations": job_events.GENERATIONS,
                            "export_limit": 1000, "raw_text_allowed": False},
        "privacy": {
            "raw_logs_included": False,
            "media_or_project_content_included": False,
            "credentials_or_settings_included": False,
            "personal_paths_included": False,
        },
    }


def build_zip(job, scheduler=None):
    buffer = io.BytesIO()
    readme = (
        "ClipForge diagnostic report\n\n"
        "This archive contains report.json with allowlisted runtime, job, provider outcome, "
        "and resource telemetry. It does not include raw logs, settings, credentials, prompts, "
        "transcripts, media, project content, or personal paths.\n\n"
        "Review the report before sharing it. ClipForge does not upload this archive.\n"
    )
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json", json.dumps(build_report(job, scheduler), ensure_ascii=False, indent=2))
        archive.writestr("README.txt", readme)
    buffer.seek(0)
    return buffer
