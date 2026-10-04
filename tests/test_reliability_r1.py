"""R1 faults use isolated disk writes and real barriers, never paid providers."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import errno
import io
import json
import os
from pathlib import Path
import threading
import wave

from fastapi import UploadFile
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend import job_store, jobs, main, media, portable, recovery_paths, store, upload_recovery
from backend.models import Project

pytestmark = pytest.mark.reliability_r1


def dispatch(job):
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    if future is not None:
        future.result(timeout=10)


def export_job(*, redo=False):
    p = store.save(Project(name="Before"))
    if redo:
        p.name = "Old branch"
        store.save(p)
        p = store.undo(p.id)
    job = jobs.reserve(p.id, "render", snapshot=p.model_dump(), config_fingerprint=main._job_config_fingerprint("render"))
    job.save_checkpoint("prior-step", "fixture", {"ready": True})
    output = store.project_dir(p.id) / "exports" / job.id / "video.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"closed export")
    calls = []

    def worker(current_job):
        def publish():
            calls.append("publish")
            latest = store.read(p.id)
            latest.name = "Committed"
            latest.exports.append({"id": job.id, "filename": output.name})
            return store.save(latest)
        saved = current_job.commit(publish, p.id, allow_latest=True)
        return {"project_id": p.id, "revision": saved.revision}

    return p, job, output, calls, worker


def fault_at(monkeypatch, boundary):
    fault = OSError(errno.ENOSPC, f"R1 {boundary}")
    original_atomic = store.atomic
    original_receipt = job_store.write_receipt
    original_persist = jobs._persist
    original_unlink = recovery_paths.unlink

    def atomic(path, value):
        match = ((boundary.startswith("history_") and path.parent.name == "history")
            or (boundary.startswith("project_") and path.name == "project.json")
            or (boundary.startswith("operation_") and path.name == ".history_operation.json"))
        if match and boundary.endswith("before"):
            raise fault
        result = original_atomic(path, value)
        if match and boundary.endswith("after"):
            raise fault
        return result

    def receipt(*args):
        if boundary == "receipt_before":
            raise fault
        original_receipt(*args)
        if boundary == "receipt_after":
            raise fault

    def persist(job):
        if boundary == "done_persist" and job.status == "done":
            raise fault
        return original_persist(job)

    def unlink(root, path):
        if boundary == "redo_remove" and path.parent.name == "redo":
            raise fault
        return original_unlink(root, path)

    monkeypatch.setattr(store, "atomic", atomic)
    monkeypatch.setattr(job_store, "write_receipt", receipt)
    monkeypatch.setattr(jobs, "_persist", persist)
    monkeypatch.setattr(recovery_paths, "unlink", unlink)


@pytest.mark.parametrize("boundary", ["history_before", "history_after", "project_before", "project_after",
    "operation_before", "operation_after", "receipt_before", "receipt_after", "done_persist", "redo_remove"])
@pytest.mark.parametrize("action", ["resume", "cancel", "startup"])
def test_commit_fault_matrix_never_republishes_or_deletes_committed_result(r0_runtime, monkeypatch, boundary, action):
    p, job, output, calls, worker = export_job(redo=True)
    monkeypatch.setitem(jobs._HANDLERS, "render", worker)
    jobs.launch(job)
    with monkeypatch.context() as faults:
        fault_at(faults, boundary)
        dispatch(job)
    committed = boundary not in {"history_before", "history_after", "project_before"}
    assert job.error["code"] == "DISK_FULL"
    assert job.commit_state == ("committed" if committed else "uncommitted")
    assert not job.resource_lease
    assert jobs._ACTIVE_RAM_BYTES == jobs._ACTIVE_DISK_BYTES == 0
    assert output.read_bytes() == b"closed export"
    written = (store.project_dir(p.id) / "project.json").read_bytes()
    attempt = job.attempt
    if action == "startup":
        jobs.initialize()
        job = jobs.JOBS[job.id]
    elif action == "cancel":
        jobs.cancel(job.id)
    else:
        jobs.resume_render(job.id, job.state_version, "changed-config" if committed else job.config_fingerprint)
        if not committed:
            dispatch(job)
    if committed:
        assert job.status == "done"
        assert job.bookkeeping_pending is False
        assert job.attempt == attempt
        assert calls == ["publish"]
        assert output.read_bytes() == b"closed export"
        assert (store.project_dir(p.id) / "project.json").read_bytes() == written
        assert sum(item["id"] == job.id for item in store.read(p.id).exports) == 1
        assert list((store.project_dir(p.id) / "redo").glob("*.json")) == []
        assert not list((store.project_dir(p.id) / ".job-commits").glob("*.json"))
        assert job_store.read_receipt(jobs.ROOT, job.id)["commit_token"] == job.commit_token
    elif action == "cancel":
        assert job.status == "cancelled"
        assert not output.exists()
        assert store.read(p.id).exports == []
    elif action == "resume":
        assert job.status == "done"
        assert calls == ["publish", "publish"]
        assert len(store.read(p.id).exports) == 1
    else:
        assert job.status == "error"
        assert output.exists()
        assert store.read(p.id).exports == []


def test_undo_after_commit_fault_is_not_applied_again_by_recovery(r0_runtime, monkeypatch):
    p, job, output, calls, worker = export_job()
    jobs.launch(job, worker)
    with monkeypatch.context() as faults:
        fault_at(faults, "operation_before")
        dispatch(job)
    assert job.commit_state == "committed"
    restored = store.undo(p.id)
    assert restored.name == "Before"
    assert job.commit_token in restored.job_commit_tokens
    redo_before = {path.name: path.read_bytes() for path in (store.project_dir(p.id) / "redo").glob("*.json")}
    revision = restored.revision
    jobs.initialize()
    jobs.resume_render(job.id, jobs.JOBS[job.id].state_version, "changed-config")
    jobs.cancel(job.id)
    assert calls == ["publish"]
    assert store.read(p.id).name == "Before"
    assert store.read(p.id).revision == revision
    assert output.exists()
    assert {path.name: path.read_bytes() for path in (store.project_dir(p.id) / "redo").glob("*.json")} == redo_before
    assert store.redo(p.id).name == "Committed"


@pytest.mark.parametrize("damage", ["intent", "receipt", "project"])
def test_unknown_commit_blocks_resume_cancel_and_storage_cleanup(r0_runtime, damage):
    p, job, output, calls, worker = export_job()
    job.commit_token = "c" * 16
    job_store.write_commit_intent(jobs.ROOT, job.id, {"job_id": job.id, "project_id": p.id, "commit_token": job.commit_token})
    job.status = "error"
    damaged = {"intent": job_store.run_dir(jobs.ROOT, job.id) / "commit-intent.json",
        "receipt": job_store.run_dir(jobs.ROOT, job.id) / "receipt.json",
        "project": store.project_dir(p.id) / "project.json"}[damage]
    damaged.write_text("{bad JSON", encoding="utf-8")
    assert jobs.reconcile_commit(job)["state"] == "unknown"
    jobs.cancel(job.id)
    with pytest.raises(ValueError, match="commit"):
        jobs.resume_render(job.id, job.state_version, job.config_fingerprint)
    with pytest.raises(ValueError, match="checkpoint"):
        portable.cleanup_cache()
    with pytest.raises(ValueError, match="checkpoint"):
        portable.trash_project(p.id)
    assert output.read_bytes() == b"closed export"
    assert job.status == "blocked"
    assert calls == []
    assert not jobs._GLOBAL_MAINTENANCE


def test_cancel_commit_race_keeps_result_and_does_not_hold_global_lock(r0_runtime):
    p, job, output, calls, _ = export_job()
    entered, release = threading.Event(), threading.Event()

    def worker(current_job):
        def publish():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("R1 test barrier timed out")
            calls.append("publish")
            latest = store.read(p.id)
            latest.exports.append({"id": job.id, "filename": output.name})
            return store.save(latest)
        saved = current_job.commit(publish, p.id)
        return {"project_id": p.id, "revision": saved.revision}

    jobs.launch(job, worker)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        worker_future = job._future
    assert entered.wait(5)
    with ThreadPoolExecutor(max_workers=2) as requests:
        try:
            cancel = requests.submit(jobs.cancel, job.id)
            # Cancellation waits on this job, leaving queue/API reads usable.
            state = requests.submit(jobs.scheduler_state).result(timeout=2)
            assert state["running"] == 1
        finally:
            release.set()
        worker_future.result(timeout=5)
        cancel.result(timeout=5)
    assert calls == ["publish"]
    assert job.status == "done"
    assert output.exists()
    assert not job.resource_lease


def test_original_worker_error_survives_status_disk_fault_and_lease_is_released(r0_runtime, monkeypatch):
    job = jobs.reserve(Project().id, "plan")

    def worker(current_job):
        monkeypatch.setattr(jobs, "_persist", lambda *_: (_ for _ in ()).throw(OSError(errno.ENOSPC, "secondary persist fault")))
        raise RuntimeError("primary worker failure")

    jobs.launch(job, worker)
    dispatch(job)
    assert job.status == "error"
    assert job.error["code"] == "INTERNAL_ERROR"
    assert job.error["safe_details"]["exception_type"] == "RuntimeError"
    assert not job.resource_lease
    assert jobs._ACTIVE_RAM_BYTES == jobs._ACTIVE_DISK_BYTES == 0
    assert not jobs._FUTURES


def upload_job(count=2):
    p = store.save(Project(name="Upload R1"))
    payload = {"role": "source", "files": [{"staged_name": f"source-{index}.png", "name": f"Source {index}"} for index in range(count)]}
    job = jobs.reserve(p.id, "upload", payload=payload, snapshot=p.model_dump(), config_fingerprint=main._job_config_fingerprint("upload"))
    stage = jobs.ROOT / "staging" / p.id / job.id / "files"
    stage.mkdir(parents=True)
    for index, item in enumerate(payload["files"]):
        Image.new("RGB", (40, 60), (20 + index, 100, 200)).save(stage / item["staged_name"])
    job.save_checkpoint("upload-received", "fixture", {"complete": True})
    return p, job, stage


@pytest.mark.parametrize("boundary", ["project_before", "project_after", "operation_before", "receipt_before"])
def test_upload_save_fault_keeps_received_or_published_bytes(r0_runtime, monkeypatch, boundary):
    p, job, stage = upload_job()
    received = {path.name: path.read_bytes() for path in stage.iterdir()}
    jobs.launch(job)
    with monkeypatch.context() as faults:
        fault_at(faults, boundary)
        dispatch(job)
    published = boundary != "project_before"
    project = store.read(p.id)
    assert bool(project.assets) == published
    assert job.error["code"] == "DISK_FULL"
    for name, content in received.items():
        source = stage / name
        destination = store.project_dir(p.id) / "assets" / name
        assert (destination if published else source).read_bytes() == content
    jobs.initialize()
    jobs.cleanup_staging()
    recovered = jobs.JOBS[job.id]
    if published:
        assert recovered.status == "done"
        jobs.cancel(job.id)
        for name, content in received.items():
            assert (store.project_dir(p.id) / "assets" / name).read_bytes() == content
        assert len(store.read(p.id).assets) == 2
    else:
        for name, content in received.items():
            assert (stage / name).read_bytes() == content
        jobs.resume_render(job.id, recovered.state_version, job.config_fingerprint)
        dispatch(recovered)
        assert recovered.status == "done"
        assert len(store.read(p.id).assets) == 2


def test_partial_upload_move_and_failed_rollback_can_resume_from_journal(r0_runtime, monkeypatch):
    p, job, stage = upload_job()
    received = {path.name: path.read_bytes() for path in stage.iterdir()}
    original_replace = os.replace

    def replace(source, destination):
        source, destination = Path(source), Path(destination)
        if source.parent == stage and source.name == "source-1.png" or source.parent.name == "assets" and destination.parent == stage:
            raise OSError(errno.ENOSPC, "R1 failed second move and rollback")
        return original_replace(source, destination)

    jobs.launch(job)
    with monkeypatch.context() as faults:
        faults.setattr(main.os, "replace", replace)
        dispatch(job)
    assert store.read(p.id).assets == []
    assert upload_recovery.load(jobs.ROOT, p.id, job.id)
    assert not (stage / "source-0.png").exists()
    assert (store.project_dir(p.id) / "assets" / "source-0.png").exists()
    jobs.initialize()
    recovered = jobs.JOBS[job.id]
    jobs.cleanup_staging()
    jobs.resume_render(job.id, recovered.state_version, job.config_fingerprint)
    dispatch(recovered)
    assert recovered.status == "done"
    assert len(store.read(p.id).assets) == 2
    for name, content in received.items():
        assert (store.project_dir(p.id) / "assets" / name).read_bytes() == content


def test_startup_retains_receiving_upload_without_checkpoint(r0_runtime):
    p = store.save(Project())
    job = jobs.reserve(p.id, "upload", phase="uploading", snapshot=p.model_dump())
    stage = r0_runtime / "staging" / p.id / job.id / "files" / "received.bin"
    stage.parent.mkdir(parents=True)
    stage.write_bytes(b"already received")
    jobs.initialize()
    jobs.cleanup_staging()
    assert stage.read_bytes() == b"already received"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction fixture")
@pytest.mark.parametrize("level", ["staging", "project", "job", "nested"])
def test_staging_cleanup_does_not_follow_external_junction(r0_runtime, tmp_path, level):
    import _winapi

    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.bin"
    sentinel.write_bytes(b"external bytes")
    targets = {"staging": r0_runtime / "staging", "project": r0_runtime / "staging" / ("a" * 16),
        "job": r0_runtime / "staging" / ("a" * 16) / ("b" * 16),
        "nested": r0_runtime / "staging" / ("a" * 16) / ("b" * 16) / "files"}
    junction = targets[level]
    junction.parent.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(external), str(junction))
    try:
        jobs.cleanup_staging()
        assert sentinel.read_bytes() == b"external bytes"
    finally:
        if junction.exists():
            junction.rmdir()  # Remove the junction entry only, never the target.


def test_cleanup_cache_does_not_hold_global_lock_or_admit_new_work(r0_runtime, monkeypatch):
    cache = r0_runtime / "cache" / "large" / "fixture.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")
    original_remove = recovery_paths.remove_tree
    checked = []

    def remove(root, path):
        with ThreadPoolExecutor(max_workers=1) as requests:
            requests.submit(jobs.scheduler_state).result(timeout=2)
        with pytest.raises(ValueError):
            jobs.reserve(Project().id, "render")
        checked.append(True)
        return original_remove(root, path)

    monkeypatch.setattr(recovery_paths, "remove_tree", remove)
    portable.cleanup_cache()
    assert checked
    assert not cache.exists()
    assert not jobs._GLOBAL_MAINTENANCE


def test_failed_second_receive_keeps_first_complete_file_after_restart(r0_runtime):
    p = store.save(Project())

    class BrokenUpload(UploadFile):
        async def read(self, size=-1):
            raise OSError(errno.ENOSPC, "R1 second receive failed")

    class Connected:
        async def is_disconnected(self):
            return False

    first = UploadFile(filename="first.png", file=io.BytesIO(b"complete first file"), size=19)
    second = BrokenUpload(filename="second.png", file=io.BytesIO(b"incomplete"), size=10)
    with pytest.raises(OSError, match="second receive"):
        asyncio.run(main.upload(Connected(), p.id, [first, second], "source", False))
    job = next(iter(jobs.JOBS.values()))
    assert job.payload["upload_incomplete"] is True
    retained = r0_runtime / "staging" / p.id / job.id / "files" / job.payload["files"][0]["staged_name"]
    jobs.initialize()
    jobs.cleanup_staging()
    assert retained.read_bytes() == b"complete first file"
    assert store.read(p.id).assets == []
    jobs.cancel(job.id)
    assert not retained.exists()


def test_cancel_upload_with_moves_left_at_destination_removes_only_owned_unpublished_files(r0_runtime, monkeypatch):
    p, job, stage = upload_job()
    original_replace = os.replace

    def replace(source, destination):
        source, destination = Path(source), Path(destination)
        if source.parent == stage and source.name == "source-1.png" or source.parent.name == "assets" and destination.parent == stage:
            raise OSError(errno.ENOSPC, "R1 move/rollback fault")
        return original_replace(source, destination)

    jobs.launch(job)
    with monkeypatch.context() as faults:
        faults.setattr(main.os, "replace", replace)
        dispatch(job)
    unrelated = store.project_dir(p.id) / "assets" / "unrelated.bin"
    unrelated.write_bytes(b"unrelated")
    jobs.cancel(job.id)
    assert job.status == "cancelled"
    assert not stage.exists()
    assert not (store.project_dir(p.id) / "assets" / "source-0.png").exists()
    assert unrelated.read_bytes() == b"unrelated"


def test_pending_receipt_can_resume_through_api_without_provider_or_new_attempt(r0_runtime, monkeypatch):
    p, job, output, calls, worker = export_job()
    jobs.launch(job, worker)
    with monkeypatch.context() as faults:
        fault_at(faults, "receipt_before")
        dispatch(job)
        detail = TestClient(main.app).get(f"/api/jobs/{job.id}").json()
        assert detail["can_resume"] is True
        assert detail["bookkeeping_pending"] is True
    monkeypatch.setitem(jobs._HANDLERS, "render", lambda *_: pytest.fail("Committed job must never execute provider/render again"))
    response = TestClient(main.app).post(f"/api/jobs/{job.id}/resume", json={"state_version": job.state_version})
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert job.attempt == 1
    assert calls == ["publish"]
    assert output.exists()


def test_concurrent_resume_completes_bookkeeping_once_without_new_attempt(r0_runtime, monkeypatch):
    p, job, output, calls, worker = export_job()
    jobs.launch(job, worker)
    with monkeypatch.context() as faults:
        fault_at(faults, "receipt_before")
        dispatch(job)
    writes = []
    original = job_store.write_receipt
    monkeypatch.setattr(job_store, "write_receipt", lambda *args: (writes.append(args[1]), original(*args))[1])
    barrier = threading.Barrier(2)
    version = job.state_version

    def resume():
        barrier.wait(timeout=5)
        return jobs.resume_render(job.id, version, "changed-config")

    with ThreadPoolExecutor(max_workers=2) as requests:
        futures = [requests.submit(resume) for _ in range(2)]
        assert all(future.result(timeout=5)["status"] == "done" for future in futures)
    assert writes == [job.id]
    assert job.attempt == 1
    assert calls == ["publish"]
    assert output.exists()


def test_history_prune_fault_finishes_without_new_revision_or_snapshot(r0_runtime, monkeypatch):
    p = store.save(Project())
    for index in range(27):
        p.name = str(index)
        store.save(p)
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump())
    original_unlink = recovery_paths.unlink

    def fail_prune(root, path):
        if path.parent.name == "history":
            raise OSError(errno.ENOSPC, "R1 history prune")
        return original_unlink(root, path)

    def worker(current_job):
        current = store.read(p.id)
        current.name = "Committed"
        saved = current_job.commit(lambda: store.save(current), p.id)
        return {"project_id": p.id, "revision": saved.revision}

    jobs.launch(job, worker)
    with monkeypatch.context() as faults:
        faults.setattr(recovery_paths, "unlink", fail_prune)
        dispatch(job)
    revision = store.read(p.id).revision
    assert job.bookkeeping_pending
    jobs.resume_render(job.id, job.state_version, "changed-config")
    assert job.status == "done"
    assert store.read(p.id).revision == revision
    assert len(list((store.project_dir(p.id) / "history").glob("*.json"))) == 25


def test_persistent_receipt_fault_retains_export_pin_until_bookkeeping_recovers(r0_runtime, monkeypatch):
    p, job, output, calls, worker = export_job()
    jobs.launch(job, worker)
    with monkeypatch.context() as faults:
        fault_at(faults, "receipt_before")
        dispatch(job)
        jobs.cancel(job.id)
        assert job.status == "error"
        assert job.commit_state == "committed"
        assert job.bookkeeping_pending
        with pytest.raises(ValueError, match="checkpoint"):
            portable.cleanup_cache()
        assert output.exists()
    cache = r0_runtime / "cache" / "fixture.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")
    portable.cleanup_cache()
    assert job.status == "done"
    assert calls == ["publish"]
    assert output.exists()
    assert not cache.exists()


@pytest.mark.parametrize("record", [[], {"entries": [None]}, {"entries": [{"source": "../outside", "destination": "assets/file", "sha256": "a" * 64}]}])
def test_invalid_upload_move_journal_keeps_bytes_and_blocks_discard(r0_runtime, record):
    p, job, stage = upload_job(count=1)
    before = (stage / "source-0.png").read_bytes()
    journal = upload_recovery.journal_path(jobs.ROOT, job.id)
    if isinstance(record, dict):
        record = {**record, "project_id": p.id, "job_id": job.id}
    store.atomic(journal, record)
    job.status = "error"
    assert jobs.reconcile_commit(job)["state"] == "unknown"
    jobs.cancel(job.id)
    assert job.status == "blocked"
    assert (stage / "source-0.png").read_bytes() == before


def test_changed_moved_upload_file_is_never_republished_or_discarded(r0_runtime):
    p, job, stage = upload_job(count=1)
    source = stage / "source-0.png"
    destination = store.project_dir(p.id) / "assets" / source.name
    destination.parent.mkdir(parents=True)
    upload_recovery.prepare(jobs.ROOT, p.id, job.id, [{"source": f"files/{source.name}",
        "destination": f"assets/{source.name}", "sha256": media.file_sha256(source)}])
    os.replace(source, destination)
    replacement = b"replacement from another writer"
    destination.write_bytes(replacement)
    jobs.launch(job)
    dispatch(job)
    assert job.status == "error"
    assert store.read(p.id).assets == []
    jobs.cancel(job.id)
    assert job.phase == "cleanup_pending"
    assert destination.read_bytes() == replacement
    assert upload_recovery.journal_path(jobs.ROOT, job.id).exists()


def test_legacy_published_export_without_token_is_preserved_on_cancel(r0_runtime):
    p, job, output, calls, worker = export_job()
    current = store.read(p.id)
    current.exports.append({"id": job.id, "filename": output.name})
    store.save(current)
    job.status = "error"
    jobs.cancel(job.id)
    assert job.status == "done"
    assert job.commit_state == "committed"
    assert calls == []
    assert output.exists()


def test_repeated_commit_reconciliation_does_not_emit_new_state_versions(r0_runtime):
    p, job, output, calls, worker = export_job()
    jobs.launch(job, worker)
    dispatch(job)
    version = job.state_version
    for _ in range(3):
        assert jobs.reconcile_commit(job)["state"] == "committed"
    assert job.state_version == version
    assert calls == ["publish"]


def test_trash_deletion_and_backup_stream_leave_scheduler_reads_available(r0_runtime, monkeypatch):
    p = store.save(Project())
    # Stream history JSON through ZIP while the per-project maintenance guard
    # is active; scheduler access must not wait for hashing/compression.
    p.name = "New name"
    store.save(p)
    original_open = portable.zipfile.ZipFile.open
    streamed = []

    def open_member(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as requests:
            requests.submit(jobs.scheduler_state).result(timeout=2)
        streamed.append(True)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(portable.zipfile.ZipFile, "open", open_member)
    archive = portable.create_backup(p.id, include_history=True)
    assert archive.is_file() and streamed
    trash = portable.trash_project(p.id)
    original_remove = recovery_paths.remove_tree
    removed = []

    def remove(root, path):
        with ThreadPoolExecutor(max_workers=1) as requests:
            requests.submit(jobs.scheduler_state).result(timeout=2)
        removed.append(path)
        return original_remove(root, path)

    monkeypatch.setattr(recovery_paths, "remove_tree", remove)
    portable.permanently_delete_trash(trash["id"])
    assert removed
    assert not (r0_runtime / "trash" / trash["id"]).exists()


def test_recovered_voice_upload_preserves_auto_assemble_metadata(r0_runtime, monkeypatch):
    p = store.save(Project(script="Giọng đọc thử."))
    payload = {"role": "voice", "auto_assemble": True, "files": [{"staged_name": "voice.wav", "name": "Voice"}]}
    job = jobs.reserve(p.id, "upload", payload=payload, snapshot=p.model_dump())
    stage = r0_runtime / "staging" / p.id / job.id / "files"
    stage.mkdir(parents=True)
    with wave.open(str(stage / "voice.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 8000)
    jobs.launch(job)
    with monkeypatch.context() as faults:
        fault_at(faults, "receipt_before")
        dispatch(job)
    assert job.bookkeeping_pending
    jobs.initialize()
    recovered = jobs.JOBS[job.id]
    assert recovered.status == "done"
    assert recovered.result["auto_assemble"] is True
    assert recovered.attempt == 1
    assert len(store.read(p.id).assets) == 1
    assert (store.project_dir(p.id) / "assets" / "voice.wav").is_file()


def test_cancel_running_uncommitted_render_waits_for_worker_then_discards_owned_export(r0_runtime):
    p, job, output, calls, worker = export_job()
    entered, release = threading.Event(), threading.Event()

    def processing(current_job):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("R1 cancel barrier timed out")
        current_job.check()
        pytest.fail("Cancelled worker must not publish")

    jobs.launch(job, processing)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert entered.wait(5)
    try:
        assert jobs.cancel(job.id)["status"] == "cancelling"
        assert output.exists()
    finally:
        release.set()
    future.result(timeout=5)
    assert job.status == "cancelled"
    assert not output.exists()
    assert store.read(p.id).exports == []
    assert calls == []
    assert not job.resource_lease


def test_repaired_unknown_evidence_allows_checkpoint_resume(r0_runtime, monkeypatch):
    p, job, output, calls, worker = export_job()
    job.status = "error"
    journal = job_store.run_dir(jobs.ROOT, job.id) / "commit-intent.json"
    store.atomic(journal, [])
    assert jobs.reconcile_commit(job)["state"] == "unknown"
    journal.unlink()
    assert jobs.reconcile_commit(job)["state"] == "uncommitted"
    assert job.status == "interrupted" and job.blocked_reason is None
    monkeypatch.setitem(jobs._HANDLERS, "render", worker)
    jobs.resume_render(job.id, job.state_version, job.config_fingerprint)
    dispatch(job)
    assert job.status == "done" and calls == ["publish"]
    assert output.exists()


def test_invalid_bookkeeping_journal_keeps_committed_output_pending(r0_runtime, monkeypatch):
    p, job, output, calls, worker = export_job()
    jobs.launch(job, worker)
    with monkeypatch.context() as faults:
        fault_at(faults, "operation_before")
        dispatch(job)
    journal = store._job_bookkeeping_path(p.id, job.commit_token)
    valid = journal.read_bytes()
    store.atomic(journal, [])
    assert jobs.reconcile_commit(job)["state"] == "committed"
    assert job.bookkeeping_pending and output.exists()
    journal.write_bytes(valid)
    jobs.resume_render(job.id, job.state_version, "changed-config")
    assert job.status == "done" and calls == ["publish"]
    assert output.exists()


def test_unreadable_job_record_keeps_upload_stage_but_still_cleans_orphan(r0_runtime):
    p, job, stage = upload_job(count=1)
    before = (stage / "source-0.png").read_bytes()
    job_store.job_path(jobs.ROOT, job.id).write_text("{broken", encoding="utf-8")
    orphan = stage.parent.parent / ("e" * 16) / "orphan.bin"
    orphan.parent.mkdir()
    orphan.write_bytes(b"orphan")
    jobs.initialize()
    assert job.id not in jobs.JOBS
    jobs.cleanup_staging()
    assert (stage / "source-0.png").read_bytes() == before
    assert not orphan.exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction fixture")
def test_bookkeeping_junction_cannot_publish_journal_outside_project(r0_runtime, tmp_path):
    import _winapi

    p, job, output, calls, worker = export_job()
    external = tmp_path / "external-journals"
    external.mkdir()
    junction = store.project_dir(p.id) / ".job-commits"
    _winapi.CreateJunction(str(external), str(junction))
    try:
        jobs.launch(job, worker)
        dispatch(job)
        assert job.status == "error"
        assert job.commit_state == "uncommitted"
        assert store.read(p.id).exports == []
        assert not list(external.iterdir())
        assert output.exists()
    finally:
        junction.rmdir()
