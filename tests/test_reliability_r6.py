"""Release gates use native artifacts, durable boundaries and real barriers."""
from concurrent.futures import ThreadPoolExecutor
import errno
import builtins
import hashlib
import json
import os
import threading
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend import job_store, jobs, main, media, portable, recovery_paths, render, store
from backend.models import Asset, Clip, Project
from tools.package_release import ARCHIVE_ROOT, build_release


def dispatch(job):
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    if future:
        future.result(timeout=20)


def native_project():
    p = Project(name="R6 native boundary", resolution="720", aspect="1:1", source_volume=0)
    p.template.caption.enabled = False
    source = store.project_dir(p.id) / "assets" / "source.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (64, 64), (20, 120, 180)).save(source)
    p.assets = [Asset(name="Fixture", filename=source.name, role="source", **media.probe(source))]
    p.clips = [Clip(asset_id=p.assets[0].id, duration=0.5)]
    return store.save(p), source


def test_missing_psutil_import_blocks_admission(r0_runtime, monkeypatch):
    original_import = builtins.__import__
    def without_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("R6 missing telemetry dependency")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_psutil)
    job = jobs.reserve(Project().id, "render")
    assert jobs._available_memory_bytes() is None
    assert "telemetry RAM" in jobs._resource_block_reason_locked(job)
    assert not job.resource_lease


@pytest.mark.parametrize("boundary", ["rename", "manifest", "project", "history", "receipt", "done"])
@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("action", ["resume", "cancel"])
def test_native_fault_restart_then_api_action_is_safe(r0_runtime, monkeypatch, boundary, side, action):
    # This matrix tests durable write faults, not workstation RAM pressure.
    # R5 and test_missing_psutil_import_blocks_admission cover resource guards.
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 8 * 1024**3)
    p, source = native_project()
    source_hash = media.file_sha256(source)
    job = jobs.reserve(p.id, "render", payload={"preview": True}, snapshot=p.model_dump(),
                       config_fingerprint=main._job_config_fingerprint("render"))
    original_replace, original_atomic = os.replace, store.atomic
    original_manifest, original_receipt, original_persist = render._save_render_manifest, job_store.write_receipt, jobs._persist
    reached = []

    def invoke(operation, matches):
        if matches and not reached and side == "before":
            reached.append(True)
            raise OSError(errno.ENOSPC, "R6 isolated write fault")
        result = operation()
        if matches and not reached and side == "after":
            reached.append(True)
            raise OSError(errno.ENOSPC, "R6 isolated write fault")
        return result

    def replace(src, dest):
        return invoke(lambda: original_replace(src, dest), boundary == "rename" and str(dest).endswith("final_video.mp4"))

    def atomic(path, value):
        matches = boundary == "project" and path.name == "project.json" and path.parent.name == p.id
        matches |= boundary == "history" and path.parent.name == "history"
        return invoke(lambda: original_atomic(path, value), matches)

    def manifest(current, value):
        return invoke(lambda: original_manifest(current, value), boundary == "manifest" and "encode-video" in value["steps"])

    def receipt(*args):
        return invoke(lambda: original_receipt(*args), boundary == "receipt")

    def persist(current):
        return invoke(lambda: original_persist(current), boundary == "done" and current.status == "done")

    jobs.launch(job)
    with monkeypatch.context() as faults:
        faults.setattr(os, "replace", replace)
        faults.setattr(store, "atomic", atomic)
        faults.setattr(render, "_save_render_manifest", manifest)
        faults.setattr(job_store, "write_receipt", receipt)
        faults.setattr(jobs, "_persist", persist)
        dispatch(job)
    assert reached and job.status == "error"
    assert not job.resource_lease and not jobs._FUTURES
    was_committed = bool(store.read(p.id).exports)
    revision_before = store.read(p.id).revision
    attempt_before = job.attempt
    jobs.initialize()  # Registry is reconstructed from durable files.
    recovered = jobs.JOBS[job.id]
    client = TestClient(main.app)
    if action == "resume":
        response = client.post(f"/api/jobs/{job.id}/resume", json={"state_version": recovered.state_version})
        assert response.status_code == 200, response.text
        if recovered.status == "queued":
            dispatch(recovered)
        assert recovered.status == "done"
    else:
        response = client.post(f"/api/jobs/{job.id}/cancel", json={})
        assert response.status_code == 200, response.text
        assert recovered.status == ("done" if was_committed else "cancelled")
    saved = store.read(p.id)
    assert len(saved.exports) == (1 if was_committed or action == "resume" else 0)
    assert media.file_sha256(source) == source_hash
    if saved.exports:
        artifact = store.project_dir(p.id) / "exports" / job.id / "preview.mp4"
        assert render._validate_video_artifact(artifact, 0.5, 720, 720)
        assert render._validate_audio_artifact(artifact, 0.5, media.audio_encoder())
        assert not list(artifact.parent.glob("*.part.mp4"))
        assert job_store.read_receipt(jobs.ROOT, job.id)["commit_token"] == recovered.commit_token
    if was_committed:
        assert saved.revision == revision_before and recovered.attempt == attempt_before
    else:
        assert saved.revision == p.revision + (action == "resume")


@pytest.mark.parametrize("first", ["cleanup", "startup"])
def test_cleanup_and_startup_overlap_without_exposing_unloaded_registry(r0_runtime, monkeypatch, first):
    cache = r0_runtime / "cache" / "fixture" / "data.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"regenerable")
    entered, release = threading.Event(), threading.Event()
    if first == "cleanup":
        original = recovery_paths.remove_tree
        def pause(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(recovery_paths, "remove_tree", pause)
        operation = portable.cleanup_cache
    else:
        original = jobs._load_scheduler_settings
        def pause():
            entered.set()
            assert release.wait(5)
            return original()
        monkeypatch.setattr(jobs, "_load_scheduler_settings", pause)
        operation = jobs.initialize
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(operation)
        assert entered.wait(5)
        try:
            if first == "cleanup":
                with pytest.raises(RuntimeError, match="bảo trì"):
                    pool.submit(jobs.initialize).result(timeout=2)
            else:
                with pytest.raises(ValueError):
                    pool.submit(portable.cleanup_cache).result(timeout=2)
            with pytest.raises(ValueError):
                jobs.reserve(Project().id, "render")
            assert jobs.scheduler_state()["running"] == 0
        finally:
            release.set()
        future.result(timeout=5)
    assert not jobs._GLOBAL_MAINTENANCE


def test_autosave_and_render_commit_use_cas_at_a_real_barrier(r0_runtime, monkeypatch):
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 8 * 1024**3)
    p = store.save(Project(name="Initial"))
    job = jobs.reserve(p.id, "render", snapshot=p.model_dump())
    job.status = "running"
    barrier = threading.Barrier(2)
    client = TestClient(main.app)
    draft = p.model_dump()
    draft["name"] = "Local draft"

    def publish():
        barrier.wait(timeout=5)
        latest = store.read(p.id)
        latest.exports.append({"id": job.id, "filename": "fixture.mp4"})
        return store.save(latest)

    def save_draft():
        barrier.wait(timeout=5)
        return client.put(f"/api/projects/{p.id}", json=draft)

    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(save_draft)
        committing = pool.submit(job.commit, publish, p.id, True)
        response = saving.result(timeout=10)
        try:
            committing.result(timeout=10)
            committed = True
        except ValueError:
            committed = False
    saved = store.read(p.id)
    assert response.status_code in (200, 409)
    if response.status_code == 200:
        assert saved.name == "Local draft"
    if committed:
        assert saved.exports == [{"id": job.id, "filename": "fixture.mp4"}]
    else:
        assert response.status_code == 200 and saved.exports == []
    assert committed or response.status_code == 200


def test_release_can_include_verified_temp_ui_without_modifying_workspace_dist(r0_runtime):
    dist = r0_runtime / "fresh-dist"
    dist.mkdir()
    (dist / "index.html").write_text("R6 verified UI", encoding="utf-8")
    (dist / "asset.js").write_text("fresh", encoding="utf-8")
    archive_path, digest = build_release(r0_runtime / "release.zip", frontend_dist=dist)
    assert digest == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read(ARCHIVE_ROOT + "/frontend/dist/index.html") == b"R6 verified UI"
        assert archive.read(ARCHIVE_ROOT + "/frontend/dist/asset.js") == b"fresh"
        assert "psutil==7.2.2" in archive.read(ARCHIVE_ROOT + "/requirements-lock.txt").decode()
        assert ARCHIVE_ROOT + "/frontend/playwright.config.cjs" in archive.namelist()
        assert ARCHIVE_ROOT + "/frontend/e2e/reliabilityR4.spec.cjs" in archive.namelist()
