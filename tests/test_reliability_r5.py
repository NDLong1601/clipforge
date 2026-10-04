"""Resource boundaries, native progress, diagnostic linkage and retry budgets."""
import json
import subprocess
import sys
import threading
import time
import zipfile

import httpx
import pytest

from backend import diagnostics, job_events, jobs, media, processes, providers, store
from backend.errors import ProviderOutcomeUnknown, ResourcePressure
from backend.models import Project

GiB = 1024**3
MiB = 1024**2


def volumes(data=20 * GiB, temporary=20 * GiB, shared=False):
    if shared:
        return {"one": {"free": data, "labels": ["data", "temp"]}}
    return {"data-volume": {"free": data, "labels": ["data"]},
            "temp-volume": {"free": temporary, "labels": ["temp"]}}


def test_admission_accounts_only_reservations_on_each_volume(r0_runtime, monkeypatch):
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 30 * GiB)
    monkeypatch.setattr(jobs, "_volume_state", lambda: volumes(3 * GiB, 500 * MiB))
    running = jobs.reserve(Project().id, "render")
    running.resource_lease = True
    running._disk_reservation = jobs._disk_request(running, jobs._volume_state())
    candidate = jobs.reserve(Project().id, "render")
    assert jobs._resource_block_reason_locked(candidate) == ""
    assert running._disk_reservation == {"data-volume": GiB, "temp-volume": 64 * MiB}
    monkeypatch.setattr(jobs, "_volume_state", lambda: volumes(20 * GiB, 300 * MiB))
    assert "temp" in jobs._resource_block_reason_locked(candidate)


def test_same_volume_combines_data_and_temp_once(r0_runtime, monkeypatch):
    job = jobs.reserve(Project().id, "render")
    assert jobs._disk_request(job, volumes(shared=True)) == {"one": GiB + 64 * MiB}


def test_ram_and_disk_hysteresis_require_recovery_margin(r0_runtime, monkeypatch):
    job = jobs.reserve(Project().id, "render")
    threshold = job.estimated_ram_bytes + jobs.RAM_FLOOR
    available = [threshold - 1]
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: available[0])
    monkeypatch.setattr(jobs, "_volume_state", lambda: volumes())
    assert "RAM" in jobs._resource_block_reason_locked(job)
    available[0] = threshold + jobs.RECOVERY_MARGIN - 1
    assert "RAM" in jobs._resource_block_reason_locked(job)
    available[0] += 1
    assert jobs._resource_block_reason_locked(job) == ""
    threshold = GiB + jobs.DISK_FLOOR
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 30 * GiB)
    monkeypatch.setattr(jobs, "_volume_state", lambda: volumes(available[0]))
    available[0] = threshold - 1
    assert "data" in jobs._resource_block_reason_locked(job)
    available[0] = threshold + jobs.RECOVERY_MARGIN - 1
    assert "data" in jobs._resource_block_reason_locked(job)
    available[0] += 1
    assert jobs._resource_block_reason_locked(job) == ""


def test_missing_disk_or_ram_telemetry_fails_closed(r0_runtime, monkeypatch):
    job = jobs.reserve(Project().id, "render")
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: None)
    assert "telemetry" in jobs._resource_block_reason_locked(job)
    with pytest.raises(ResourcePressure, match="TELEMETRY_UNAVAILABLE"):
        job.start_step("encode-video")
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 30 * GiB)
    monkeypatch.setattr(jobs, "_volume_state", lambda: {})
    assert "telemetry" in jobs._resource_block_reason_locked(job)


def test_native_mid_encode_disk_pressure_keeps_checkpoint_and_releases_lease(r0_runtime, monkeypatch):
    project = store.save(Project(name="Unchanged"))
    job = jobs.reserve(project.id, "render", snapshot=project.model_dump())
    space = [20 * GiB]
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 30 * GiB)
    monkeypatch.setattr(jobs, "_volume_state", lambda: volumes(space[0]))
    saw_progress = threading.Event()
    original = job.update_encode_progress

    def progress(*args):
        original(*args)
        saw_progress.set()
        space[0] = 100 * MiB

    monkeypatch.setattr(job, "update_encode_progress", progress)

    def encode(current):
        current.save_checkpoint("fixture", "valid", {"completed": True})
        current.start_step("encode-video")
        media.run_ff(["-re", "-f", "lavfi", "-i", "color=s=64x64:r=10", "-t", "6",
                      "-an", "-c:v", "libx264", r0_runtime / "partial.mp4"], job=current)

    jobs.launch(job, encode)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    future.result(timeout=12)
    assert saw_progress.is_set()
    assert job.status == "interrupted" and job.phase == "resource_wait"
    assert job.error["code"] == "DISK_LOW" and job.error["step_id"] == "encode-video"
    assert job.error["diagnostic_recorded"] is True
    assert "resume_when_ready" in job.error["actions"]
    assert job.load_checkpoint("fixture", "valid") == {"completed": True}
    assert store.read(project.id).revision == project.revision
    assert not job.resource_lease and not job._disk_reservation
    assert jobs._ACTIVE_RAM_BYTES == jobs._ACTIVE_DISK_BYTES == 0
    assert all(value == 0 for value in jobs._ACTIVE_RESOURCE_USE.values())
    assert processes.active_count() == 0
    assert not any(t.name.startswith("clipforge-ff-") for t in threading.enumerate())
    candidate = jobs.reserve(Project().id, "render")
    assert "data" in jobs._resource_block_reason_locked(candidate)
    events = diagnostics.build_report(job)["events"]
    assert any(event.get("diagnostic_id") == job.error["diagnostic_id"] for event in events)


def test_unknown_internal_error_events_and_zip_exclude_sentinel(r0_runtime):
    sentinel = "PRIVATE_SENTINEL_57_dont_export"
    job = jobs.reserve(Project().id, "plan", payload={"prompt": sentinel})
    job.start_step("plan")
    job.error = jobs._job_error(job, RuntimeError(sentinel))
    assert job.error["diagnostic_recorded"]
    with zipfile.ZipFile(diagnostics.build_zip(job)) as archive:
        report = json.loads(archive.read("report.json"))
        assert sentinel.encode() not in b"".join(archive.read(name) for name in archive.namelist())
    assert report["job"]["error"]["step_id"] == "plan"
    assert report["events"][-1]["diagnostic_id"] == job.error["diagnostic_id"]


def test_event_rotation_and_allowlist_are_bounded(r0_runtime, monkeypatch):
    monkeypatch.setattr(job_events, "MAX_BYTES", 600)
    job = jobs.reserve(Project().id, "plan")
    job.current_step = "PRIVATE_SENTINEL"
    for _ in range(100):
        job_events.emit(r0_runtime, job, "step_started", prompt="PRIVATE_SENTINEL", headers={"secret": "PRIVATE_SENTINEL"})
    paths = list((r0_runtime / "job_runs" / job.id).glob("events.jsonl*"))
    assert len(paths) == 3 and all(path.stat().st_size <= 600 for path in paths)
    assert b"PRIVATE_SENTINEL" not in b"".join(path.read_bytes() for path in paths)
    assert job_events.read(r0_runtime, job.id)[-1]["step_id"] == "unknown"


def test_eta_requires_duration_and_sufficient_progress(r0_runtime):
    job = jobs.Job(Project().id, "render")
    for duration, elapsed, samples in ((None, 5, 3), (10, 0.5, 3), (10, 3, 1)):
        job.update_encode_progress(2, duration, elapsed, samples)
        assert job.encode_progress["eta_seconds"] is None
    job.update_encode_progress(2, 10, 3, 3)
    assert job.encode_progress["eta_seconds"] == 12 and job.encode_progress["eta_estimated"]


def test_cpu_fallback_requires_cpu_in_whole_job_lease(r0_runtime):
    render_job = jobs.Job(Project().id, "render")
    render_job.resource_lease = True
    render_job.cpu_fallback()
    other = jobs.Job(Project().id, "voice")
    other.resource_lease = True
    with pytest.raises(ResourcePressure, match="CPU_SLOT_UNAVAILABLE"):
        other.cpu_fallback()


def test_ffmpeg_continuously_drains_large_stderr_and_progress(r0_runtime, monkeypatch):
    real_popen = subprocess.Popen
    generated = []
    script = "import sys,time; sys.stderr.write('PRIVATE_SENTINEL'*1000000); sys.stderr.flush(); print('out_time_us=1000000',flush=True); time.sleep(.6); print('out_time_us=2000000',flush=True); time.sleep(.6)"

    def noisy_command(arguments, **kwargs):
        generated.extend(arguments)
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(media.subprocess, "Popen", noisy_command)
    job = jobs.Job(Project().id, "render")
    media.run_ff(["-t", "5", "unused.mp4"], job=job, timeout=5)
    assert "-progress" in generated and "pipe:1" in generated
    assert job.encode_progress["processed_seconds"] >= 1
    assert job.encode_progress["duration_seconds"] == 5
    assert processes.active_count() == 0
    assert not any(t.name.startswith("clipforge-ff-") for t in threading.enumerate())
    assert "PRIVATE_SENTINEL" not in json.dumps(diagnostics.build_report(job))


@pytest.mark.parametrize("timeout", [False, True])
def test_retry_budget_is_separate_from_unknown_outcome(r0_runtime, monkeypatch, timeout):
    store.save_settings({"ai_profiles": [{"id": str(i), "name": "Fixture", "provider": "openai",
        "api_key": "PRIVATE_SENTINEL", "model": "fixture", "enabled": True} for i in range(4)],
        "active_ai_profile_id": "0", "ai_auto_fallback": True})
    requests = []
    real_client = httpx.Client

    def respond(request):
        requests.append(request)
        if timeout:
            raise httpx.ReadTimeout("PRIVATE_SENTINEL", request=request)
        return httpx.Response(503, json={"error": "PRIVATE_SENTINEL"})

    monkeypatch.setattr(providers.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    job = jobs.reserve(Project().id, "plan")
    with pytest.raises(ProviderOutcomeUnknown if timeout else ValueError):
        providers.ai_json("PRIVATE_SENTINEL", job=job)
    assert len(requests) == (1 if timeout else 4)
    if timeout:
        assert job.provider_intents[0]["status"] == "outcome_unknown"
    else:
        assert all(intent["status"] != "outcome_unknown" for intent in job.provider_intents)
