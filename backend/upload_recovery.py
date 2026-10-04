"""Durable ownership of upload moves; rollback returns bytes instead of deleting."""

import json
import os
import re
from pathlib import Path

from . import media, recovery_paths, store


def journal_path(root, job_id):
    return recovery_paths.checked_path(root, root / "job_runs" / job_id / "upload-moves.json")


def prepare(root, pid, job_id, entries):
    store.atomic(journal_path(root, job_id), {"schema_version": 1, "project_id": pid, "job_id": job_id, "entries": entries})


def load(root, pid, job_id):
    path = journal_path(root, job_id)
    if not path.exists():
        return []
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("project_id") != pid or record.get("job_id") != job_id or not isinstance(record.get("entries"), list):
        raise ValueError("Journal di chuyển upload không hợp lệ")
    entries = record["entries"]
    for entry in entries:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("source", "destination", "sha256")) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ValueError("Journal di chuyển upload thiếu thông tin sở hữu file")
        source, dest = Path(entry["source"]), Path(entry["destination"])
        if len(source.parts) != 2 or len(dest.parts) != 2 or source.name != dest.name or dest.name in {".", "..", ""} or (
            source.parts[0], dest.parts[0]) not in {("files", "assets"), ("thumbs", "thumbs")}:
            raise ValueError("Journal di chuyển upload chứa đường dẫn không hợp lệ")
    return entries


def restore(root, pid, job_id, *, discard=False):
    entries = load(root, pid, job_id)
    with store.LOCK:
        project = store.read(pid)
        published = {f"assets/{asset.filename}" for asset in project.assets}
        published.update(f"thumbs/{asset.thumbnail}" for asset in project.assets if asset.thumbnail)
    stage = root / "staging" / pid / job_id
    project_dir = store.project_dir(pid)
    for entry in entries:
        source = recovery_paths.checked_path(root, stage / entry["source"])
        destination = recovery_paths.checked_path(root, project_dir / entry["destination"])
        if entry["destination"] in published or not destination.is_file():
            continue
        # Hash outside scheduler/store locks. Only journal-owned bytes can be
        # returned or discarded, never a file that has since been replaced.
        if media.file_sha256(destination) != entry["sha256"]:
            raise ValueError("File upload đã đổi sau khi di chuyển; giữ dữ liệu để kiểm tra")
        if discard:
            recovery_paths.unlink(root, destination)
        elif not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, source)
    if discard:
        recovery_paths.remove_tree(root, stage)
        recovery_paths.unlink(root, journal_path(root, job_id))
