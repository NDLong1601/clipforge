"""Desired behavior, not assertions that celebrate the current defects.

Known red tests are strict xfails limited to AssertionError. --runxfail exposes
the raw red baseline; unexpected setup/runtime failures remain real failures.
"""

from __future__ import annotations

import ast
import errno
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from backend import job_store, jobs, main, media, portable, render, store
from backend.models import Project
from tests.fixtures.factory import make_media_project


pytestmark = pytest.mark.reliability_r0
REPO = Path(__file__).resolve().parents[1]


def red(finding):
    return pytest.mark.xfail(reason=f"{finding}: open finding, fix in R1-R5", strict=True, raises=AssertionError)


@pytest.mark.parametrize("status", ["interrupted", "error", "queued"])
def test_f1_startup_preserves_upload_stage_and_removes_only_orphans(r0_runtime, status):
    project = store.save(Project(name="R0 upload stage"))
    job = jobs.reserve(project.id, "upload", payload={"files": [
        {"staged_name": "complete.png"}, {"staged_name": "partial.png"},
    ]}, snapshot=project.model_dump())
    stage = r0_runtime / "staging" / project.id / job.id / "files"
    stage.mkdir(parents=True)
    received = {"complete.png": b"complete upload", "partial.png": b"partial upload"}
    for name, content in received.items():
        (stage / name).write_bytes(content)
    orphan = stage.parents[1] / ("f" * 16) / "files" / "orphan.bin"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"unreferenced")
    job.save_checkpoint("upload-received", "fixture", {"complete": True})
    job.status = status
    jobs._persist(job)

    jobs.initialize()
    recovered = jobs.JOBS[job.id]
    assert recovered.resume_capability == "checkpointed"
    jobs.cleanup_staging()

    assert all((stage / name).is_file() for name in received), "F1: startup deleted pinned staging/<pid>/<jid>"
    assert {name: (stage / name).read_bytes() for name in received} == received
    assert not orphan.exists()


def test_staging_cleanup_removes_unreferenced_project_directory(r0_runtime):
    orphan = r0_runtime / "staging" / ("e" * 16) / ("f" * 16) / "files" / "orphan.bin"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"unreferenced")
    jobs.cleanup_staging()
    assert not orphan.parents[2].exists()


def test_f2_cancel_after_receipt_failure_preserves_published_export(r0_runtime, monkeypatch):
    project = store.save(Project(name="R0 commit receipt fault"))
    job = jobs.reserve(project.id, "render", snapshot=project.model_dump())
    job.status = "running"
    export = store.project_dir(project.id) / "exports" / job.id / "video.mp4"
    export.parent.mkdir(parents=True)
    content = b"published export sentinel"
    export.write_bytes(content)

    def handler(current_job):
        def publish():
            current = store.read(project.id)
            current.exports.append({"id": current_job.id, "filename": "video.mp4"})
            return store.save(current)
        return current_job.commit(publish, project.id, allow_latest=True)

    with monkeypatch.context() as fault:
        fault.setattr(job_store, "write_receipt", lambda *args: (_ for _ in ()).throw(OSError(errno.ENOSPC, "R0 receipt fault")))
        jobs._run_admitted_job(job, handler)
    published = store.read(project.id)
    assert any(item["id"] == job.id for item in published.exports)
    assert job.commit_token in published.job_commit_tokens
    revision = published.revision

    response = TestClient(main.app).post(f"/api/jobs/{job.id}/cancel", json={})
    assert response.status_code == 200
    assert export.is_file(), "F2: cancel deleted an export already published by the project"
    assert export.read_bytes() == content
    assert store.read(project.id).revision == revision
    assert any(item["id"] == job.id for item in store.read(project.id).exports)


def test_f4_queued_snapshot_can_be_started_with_same_id_after_restart(r0_runtime, monkeypatch):
    project = store.save(Project(name="R0 queued snapshot"))
    config = main._job_config_fingerprint("render")
    calls = []
    monkeypatch.setitem(jobs._HANDLERS, "render", lambda job: calls.append(job.id))
    job = jobs.reserve(project.id, "render", payload={"preview": True}, snapshot=project.model_dump(), config_fingerprint=config)
    jobs.launch(job)
    jobs.initialize()
    recovered = jobs.JOBS[job.id]
    assert recovered.status == "interrupted"
    assert calls == [], "Recovery must never automatically start saved work"
    try:
        result = jobs.resume_render(job.id, recovered.state_version, config)
        rejection = None
    except ValueError as error:
        result, rejection = None, str(error)
    assert rejection is None, f"F4: queued snapshot cannot be started: {rejection}"
    assert result["id"] == job.id
    assert jobs.JOBS[job.id].attempt == 2
    assert result["status"] == "queued"
    assert calls == []


@pytest.mark.parametrize("change_at", ["before_enqueue", "after_enqueue_before_lease"])
def test_f6_stale_revision_blocks_before_provider_call(r0_runtime, monkeypatch, change_at):
    project = store.save(Project(name="R0 revision precheck", script="Original snapshot"))
    config = main._job_config_fingerprint("voice")
    job = jobs.reserve(project.id, "voice", snapshot=project.model_dump(), config_fingerprint=config)
    job.save_checkpoint("prior-step", "fixture", {"complete": True})
    job.status = "error"
    jobs._persist(job)
    calls = []
    monkeypatch.setattr(main.providers, "synthesize", lambda snapshot, current_job: (calls.append(snapshot.script), snapshot)[1])

    def edit_project():
        changed = store.read(project.id)
        changed.script = "Edited after snapshot"
        store.save(changed)

    if change_at == "before_enqueue":
        edit_project()
    try:
        jobs.resume_render(job.id, job.state_version, config)
    except ValueError:
        pass
    if change_at == "after_enqueue_before_lease":
        edit_project()
    if job.status == "queued":
        with jobs.LOCK:
            jobs._dispatch_one_locked(job)
            future = job._future
        if future is not None:
            future.result(timeout=10)

    assert calls == [], "F6: provider was called using a revision that cannot be committed"
    assert job.status == "blocked"
    assert (job.blocked_reason or job.error)["code"] == "REVISION_CONFLICT"
    assert job.load_checkpoint("prior-step", "fixture") == {"complete": True}
    assert store.read(project.id).script == "Edited after snapshot"
    assert job.resource_lease is False


@pytest.mark.parametrize("dependency", ["source_content", "renderer_version"])
def test_f7_completed_mux_revalidates_current_dependencies(r0_runtime, monkeypatch, dependency):
    project = make_media_project(include_voice=False, include_music=False)
    config = main._job_config_fingerprint("render")
    job = jobs.reserve(project.id, "render", snapshot=project.model_dump(), config_fingerprint=config)
    original = render.render(project, job, preview=True)
    export = store.project_dir(project.id) / "exports" / job.id / "preview.mp4"
    output_hash = media.file_sha256(export)
    if dependency == "source_content":
        source = store.asset_path(project.id, project.assets[0])
        source_hash = media.file_sha256(source)
        media.run_ff(["-f", "lavfi", "-i", "color=c=blue:size=240x320:rate=30", "-t", "5", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", source])
        assert source_hash != media.file_sha256(source)
    else:
        monkeypatch.setattr(render, "RENDER_VERSION", "r0-new-renderer")
    calls = []
    original_ff = render.run_ff

    def counted_ff(*args, **kwargs):
        calls.append(args)
        return original_ff(*args, **kwargs)

    monkeypatch.setattr(render, "run_ff", counted_ff)
    try:
        resumed = render.render(project, job, preview=True)
    except ValueError as error:
        # Rejecting a changed snapshot dependency is also a safe result.
        assert re.search(r"snapshot|source|nguồn|fingerprint|checkpoint|dependency|đã đổi", str(error), re.I)
        return
    assert calls, f"F7: completed mux reused without checking changed {dependency}"
    if dependency == "source_content":
        assert media.file_sha256(export) != output_hash
    else:
        assert resumed["render_version"] != original["render_version"]
        assert resumed["render_version"] == "r0-new-renderer"


def test_f8_optional_telemetry_import_is_declared_and_locked(r0_runtime):
    tree = ast.parse((REPO / "backend" / "jobs.py").read_text(encoding="utf-8"))
    uses_psutil = any(isinstance(node, ast.Import) and any(alias.name == "psutil" for alias in node.names) for node in ast.walk(tree))
    if not uses_psutil:
        assert isinstance(jobs._available_memory_bytes(), int)
        return
    requirements = (REPO / "requirements.txt").read_text(encoding="utf-8")
    locked = (REPO / "requirements-lock.txt").read_text(encoding="utf-8")
    assert re.search(r"(?mi)^psutil\b", requirements), "F8: installed application does not declare its telemetry dependency"
    assert re.search(r"(?mi)^psutil==\d[^\s]*\s*$", locked), "F8: telemetry dependency needs an exact tested lock"


def test_f8_missing_ram_telemetry_has_explicit_admission_block(r0_runtime, monkeypatch):
    job = jobs.reserve(Project().id, "render")
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: None)
    monkeypatch.setattr(jobs, "_disk_available_bytes", lambda: 100 * 1024**3)
    with jobs.LOCK:
        reason = jobs._resource_block_reason_locked(job)
    assert reason, "F8: missing RAM telemetry silently permits heavy work"


def test_low_ram_defers_admission_without_taking_lease(r0_runtime, monkeypatch):
    job = jobs.reserve(Project().id, "render")
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 128 * 1024**2)
    monkeypatch.setattr(jobs, "_disk_available_bytes", lambda: 100 * 1024**3)
    with jobs.LOCK:
        reason = jobs._resource_block_reason_locked(job)
    assert "RAM" in reason
    assert job.resource_lease is False
    assert jobs._ACTIVE_RAM_BYTES == 0


@pytest.mark.parametrize("status", ["error", "interrupted", "blocked"])
def test_f9_backend_discard_releases_pin_and_preserves_source_and_prior_export(r0_runtime, status):
    project = store.save(Project(name="R0 discard storage guard"))
    project.exports.append({"id": "e" * 16, "filename": "video.mp4"})
    project = store.save(project)
    base = store.project_dir(project.id)
    source = base / "assets" / "original.bin"
    published = base / "exports" / ("e" * 16) / "video.mp4"
    for path, content in [(source, b"original source"), (published, b"prior published export")]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    job = jobs.reserve(project.id, "render", snapshot=project.model_dump())
    job.save_checkpoint("prior-step", "fixture", {"complete": True})
    job.status = status
    jobs._persist(job)
    cache = r0_runtime / "cache" / "recovery.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"pinned")
    with pytest.raises(ValueError, match="giữ checkpoint"):
        portable.cleanup_cache()

    response = TestClient(main.app).post(f"/api/jobs/{job.id}/cancel", json={})
    assert response.status_code == 200
    assert jobs.JOBS[job.id].status == "cancelled"
    assert jobs.has_pinned_artifacts(project.id) is False
    assert portable.cleanup_cache()["deleted_bytes"] == len(b"pinned")
    assert not cache.exists()
    assert source.read_bytes() == b"original source"
    assert published.read_bytes() == b"prior published export"
