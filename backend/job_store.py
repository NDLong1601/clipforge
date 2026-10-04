"""Durable job documents, immutable input snapshots, and commit receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import atomic

JOB_SCHEMA_VERSION = 2


def fingerprint(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def job_path(root: Path, job_id: str) -> Path:
    return root / "jobs" / f"{job_id}.json"


def run_dir(root: Path, job_id: str) -> Path:
    return root / "job_runs" / job_id


def pipeline_path(root: Path, job_id: str) -> Path:
    return run_dir(root, job_id) / "pipeline-checkpoints.json"


def provider_result_path(root: Path, job_id: str, call_id: str) -> Path:
    return run_dir(root, job_id) / "provider-results" / f"{call_id}.json"


def write_provider_result(root: Path, job_id: str, call_id: str, result: dict) -> str:
    path = provider_result_path(root, job_id, call_id)
    atomic(path, result)
    return path.relative_to(root).as_posix()


def read_provider_result(root: Path, relative_path: str) -> dict | None:
    path = (root / relative_path).resolve()
    base = (root / "job_runs").resolve()
    if path != base and base not in path.parents:
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def persist(root: Path, record: dict) -> None:
    atomic(job_path(root, record["id"]), record)


def write_snapshot(root: Path, job_id: str, snapshot: dict) -> tuple[str, str]:
    path = run_dir(root, job_id) / "snapshot.json"
    atomic(path, snapshot)
    return path.relative_to(root).as_posix(), fingerprint(snapshot)


def write_commit_intent(root: Path, job_id: str, intent: dict) -> None:
    atomic(run_dir(root, job_id) / "commit-intent.json", intent)


def read_commit_intent(root: Path, job_id: str) -> dict | None:
    path = run_dir(root, job_id) / "commit-intent.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def write_receipt(root: Path, job_id: str, receipt: dict) -> None:
    atomic(run_dir(root, job_id) / "receipt.json", receipt)


def read_receipt(root: Path, job_id: str) -> dict | None:
    path = run_dir(root, job_id) / "receipt.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def load_snapshot(root: Path, relative_path: str) -> dict:
    path = (root / relative_path).resolve()
    base = root.resolve()
    if path != base and base not in path.parents:
        raise ValueError("Đường dẫn snapshot nằm ngoài thư mục dữ liệu")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Snapshot job không hợp lệ")
    return data
