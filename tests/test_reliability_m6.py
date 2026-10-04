from __future__ import annotations

import errno
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import diagnostics, job_store, jobs, portable, render, store
from backend.main import app
from backend.models import Project
from tests.fixtures.factory import make_media_project


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    monkeypatch.setattr(jobs, "_SCHEDULER_PAUSED", True)
    monkeypatch.setattr(jobs, "_SCHEDULER_STOP", False)
    monkeypatch.setattr(jobs, "_ACTIVE_RESOURCE_USE", {})
    monkeypatch.setattr(jobs, "_ACTIVE_RAM_BYTES", 0)
    monkeypatch.setattr(jobs, "_ACTIVE_DISK_BYTES", 0)
    monkeypatch.setattr(jobs, "_RUNNING_PROJECTS", set())
    jobs.JOBS.clear()
    jobs._RUNTIME_HANDLERS.clear()
    jobs._FUTURES.clear()
    yield tmp_path
    jobs.JOBS.clear()
    jobs._RUNTIME_HANDLERS.clear()
    jobs._FUTURES.clear()


def test_render_restarts_at_failed_mux_without_publishing_partial_mp4(isolated, monkeypatch):
    project = make_media_project(include_voice=False, include_music=False)
    job = jobs.Job(project.id, "render", input_fingerprint="m6-fault-input")
    original_run_ff = render.run_ff
    failed = False

    def fail_once_at_mux(args, *positional, **kwargs):
        nonlocal failed
        if not failed and "-map" in args and "1:a:0" in args and "-c" in args and "copy" in args:
            failed = True
            raise OSError(errno.ENOSPC, "simulated disk full during final mux")
        return original_run_ff(args, *positional, **kwargs)

    monkeypatch.setattr(render, "run_ff", fail_once_at_mux)
    export_dir = store.project_dir(project.id) / "exports" / job.id
    with pytest.raises(OSError, match="simulated disk full"):
        render.render(project, job, preview=True)

    manifest_path = jobs.ROOT / "job_runs" / job.id / "render-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "clip:000" in manifest["steps"]
    assert "encode-video" in manifest["steps"]
    assert not (export_dir / "preview.mp4").exists()
    assert not list(export_dir.glob("*.part.mp4"))

    monkeypatch.setattr(render, "run_ff", original_run_ff)
    result = render.render(project, job, preview=True)

    assert result["clip_cache_hits"] == 1
    assert result["clip_cache_misses"] == 0
    assert (export_dir / "preview.mp4").is_file()
    assert render._validate_audio_artifact(
        export_dir / "preview.mp4", 5, result["audio_encoder"]
    )


def test_project_commit_recovers_after_write_before_receipt_without_second_save(isolated, monkeypatch):
    project = store.save(Project(name="Commit trước fault"))
    job = jobs.reserve(project.id, "plan", snapshot=project.model_dump())
    calls = []
    original_write_receipt = job_store.write_receipt

    def crash_before_receipt(root, job_id, receipt):
        raise OSError(errno.ENOSPC, "simulated crash before receipt")

    monkeypatch.setattr(job_store, "write_receipt", crash_before_receipt)

    def save_result():
        calls.append("save")
        changed = store.read(project.id)
        changed.name = "Commit đã ghi"
        return store.save(changed)

    with pytest.raises(OSError, match="simulated crash"):
        job.commit(save_result, project.id)

    persisted_revision = store.read(project.id).revision
    history_before_recovery = list((store.project_dir(project.id) / "history").glob("*.json"))
    monkeypatch.setattr(job_store, "write_receipt", original_write_receipt)
    jobs.initialize()

    recovered = jobs.JOBS[job.id]
    receipt = job_store.read_receipt(jobs.ROOT, job.id)
    assert calls == ["save"]
    assert store.read(project.id).name == "Commit đã ghi"
    assert store.read(project.id).revision == persisted_revision
    assert list((store.project_dir(project.id) / "history").glob("*.json")) == history_before_recovery
    assert recovered.status == "done"
    assert receipt["recovered"] is True


def test_unknown_provider_outcome_stays_blocked_until_cost_confirmation(isolated):
    project = store.save(Project(name="Provider outcome"))
    job = jobs.reserve(project.id, "plan", snapshot=project.model_dump())
    job.status = "running"
    job.phase = "processing"
    job.provider_intents = [{
        "id": "a" * 16,
        "fingerprint": "request-fingerprint",
        "provider": "mock",
        "model": "test",
        "operation": "plan",
        "status": "sending",
        "started_at": job.created_at,
    }]
    jobs._persist(job)

    jobs.initialize()
    recovered = jobs.JOBS[job.id]
    client = TestClient(app)
    detail = client.get(f"/api/jobs/{job.id}").json()
    response = client.post(
        f"/api/jobs/{job.id}/retry-unknown",
        json={"state_version": recovered.state_version},
    )

    assert recovered.status == "blocked"
    assert recovered.attempt == 1
    assert recovered.provider_intents[0]["status"] == "outcome_unknown"
    assert detail["can_retry_unknown"] is True
    assert response.status_code == 422
    assert jobs.JOBS[job.id].status == "blocked"


def test_concurrent_duplicate_resume_creates_only_one_attempt(isolated):
    project = store.save(Project(name="Resume race"))
    from backend import main
    config = main._job_config_fingerprint("plan")
    job = jobs.reserve(project.id, "plan", snapshot=project.model_dump(), config_fingerprint=config)
    job.resume_capability = "checkpointed"
    job.status, job.phase = "interrupted", "interrupted"
    job.save_checkpoint("plan", "step-fingerprint", {"ready": True})
    jobs._persist(job)
    first_state = job.state_version

    first = jobs.resume_render(job.id, first_state, config)
    second = jobs.resume_render(job.id, first_state, config)

    assert first["id"] == second["id"] == job.id
    assert jobs.JOBS[job.id].attempt == 2
    assert jobs.JOBS[job.id].status == "queued"

    job.status, job.phase = "interrupted", "interrupted"
    job.state_version += 1
    jobs._persist(job)
    with pytest.raises(ValueError, match="đã đổi trạng thái"):
        jobs.resume_render(job.id, first_state, config)


def test_disk_full_failure_releases_scheduler_lease(isolated):
    project = store.save(Project(name="Disk fault"))
    job = jobs.reserve(project.id, "render")
    launched = jobs.launch(
        job,
        lambda _job: (_ for _ in ()).throw(OSError(errno.ENOSPC, "simulated full disk")),
    )
    assert launched["status"] == "queued"

    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
    job._future.result(timeout=10)

    state = jobs.scheduler_state()
    assert job.status == "error"
    assert job.error["code"] == "DISK_FULL"
    assert job.resource_lease is False
    assert state["reserved_ram_bytes"] == 0
    assert state["reserved_disk_bytes"] == 0
    assert not any(state["resource_use"].values())


def test_active_queue_keeps_more_than_one_hundred_jobs_and_hides_payload(isolated):
    total = 105
    for index in range(total):
        job = jobs.reserve(f"{index + 1:016x}", "render", payload={"secret": "do-not-send"})
        jobs.launch(job, lambda _job: None)

    response = TestClient(app).get("/api/jobs/queue")
    body = response.json()

    assert response.status_code == 200
    assert body["queued"] == total
    assert len(body["jobs"]) == total
    assert all("payload" not in item for item in body["jobs"])
    assert "do-not-send" not in response.text


def test_cleanup_cache_preserves_checkpoint_until_job_is_cancelled(isolated):
    project = store.save(Project(name="Pinned checkpoint"))
    job = jobs.reserve(project.id, "plan")
    job.status = "interrupted"
    job.resume_capability = "checkpointed"
    jobs._persist(job)
    cache_file = store.ROOT / "cache" / "checkpoint-cache.bin"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"pinned")

    with pytest.raises(ValueError, match="giữ checkpoint"):
        portable.cleanup_cache()
    assert cache_file.exists()

    jobs.cancel(job.id)
    result = portable.cleanup_cache()
    assert result["deleted_bytes"] == len(b"pinned")
    assert not cache_file.exists()


def test_diagnostic_zip_is_allowlisted_against_secret_prompt_and_path(isolated, monkeypatch):
    monkeypatch.setattr(diagnostics, "_ffmpeg_version", lambda: "7.1")
    job = jobs.Job("1234567890abcdef", "plan", payload={
        "prompt": "PRIVATE_PROMPT_SENTINEL",
        "path": r"C:\Users\Private User\secret.txt",
        "api_key": "sk-0123456789-secret",
    })
    job.message = "raw message PRIVATE_PROMPT_SENTINEL"
    job.error = {
        "code": "INTERNAL_ERROR",
        "message": "PRIVATE_PROMPT_SENTINEL at C:\\Users\\Private User\\secret.txt",
        "safe_details": {"exception_type": "RuntimeError"},
    }
    job.provider_calls = [{"provider": "https://example.invalid?token=secret", "request": "PRIVATE_PROMPT_SENTINEL"}]

    archive = diagnostics.build_zip(job, {"queued": 0}).getvalue()
    with zipfile.ZipFile(io.BytesIO(archive)) as report_zip:
        assert set(report_zip.namelist()) == {"report.json", "README.txt"}
        data = report_zip.read("report.json").decode("utf-8")
        readme = report_zip.read("README.txt").decode("utf-8")

    for private_value in ("PRIVATE_PROMPT_SENTINEL", "secret.txt", "sk-0123456789-secret", "example.invalid"):
        assert private_value not in data
        assert private_value not in readme
