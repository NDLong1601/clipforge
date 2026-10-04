"""R2 exercises real workers and owned Windows processes in isolated data."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

from fastapi.testclient import TestClient
import pytest

from backend import job_store, jobs, main, media, processes, providers, store
from backend.models import AIProfile, Project, Settings

pytestmark = pytest.mark.reliability_r2
REPO = Path(__file__).resolve().parents[1]


def dispatch(job):
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert future is not None
    future.result(timeout=10)


def test_restart_ten_queued_and_running_jobs_only_runs_after_explicit_action(r0_runtime, monkeypatch):
    ids = []
    calls = []
    for kind in ("render", "plan"):
        monkeypatch.setitem(jobs._HANDLERS, kind, lambda job: calls.append(job.id))
    for index in range(10):
        project = store.save(Project(name=f"Queue {index}"))
        kind = "plan" if index >= 7 else "render"
        job = jobs.reserve(project.id, kind, snapshot=project.model_dump(), config_fingerprint=main._job_config_fingerprint(kind))
        jobs.launch(job)
        if index >= 7:
            job.save_checkpoint("prior", "r2-fixture", {"ready": True})
        else:
            job.resume_capability = "restart_only"  # Records from before R2.
        job.status = "queued" if index < 6 else "running" if index < 8 else "cancelling"
        jobs._persist(job)
        ids.append(job.id)
    jobs.initialize()
    assert calls == []
    for index, jid in enumerate(ids):
        detail = TestClient(main.app).get(f"/api/jobs/{jid}").json()
        assert detail["status"] == "interrupted" and detail["can_resume"]
        assert detail["resume_capability"] == ("snapshot" if index < 7 else "checkpointed")
        response = TestClient(main.app).post(f"/api/jobs/{jid}/resume", json={"state_version": detail["state_version"]})
        assert response.status_code == 200 and response.json()["attempt"] == 2
    assert calls == []
    for jid in ids:
        dispatch(jobs.JOBS[jid])
    assert calls == ids
    assert all(job.status == "done" and not job.resource_lease for job in jobs.JOBS.values())


@pytest.mark.parametrize("checkpointed", [False, True])
def test_two_clients_start_or_resume_create_one_attempt_and_worker(r0_runtime, monkeypatch, checkpointed):
    p = store.save(Project())
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump(), config_fingerprint=main._job_config_fingerprint("plan"))
    if checkpointed:
        job.save_checkpoint("prior", "r2", {"ready": True})
    job.status = "interrupted"
    jobs._persist(job)
    version = job.state_version
    calls = []
    monkeypatch.setitem(jobs._HANDLERS, "plan", lambda current: calls.append(current.attempt))
    barrier = threading.Barrier(2)

    def resume():
        barrier.wait(timeout=5)
        return TestClient(main.app).post(f"/api/jobs/{job.id}/resume", json={"state_version": version})

    with ThreadPoolExecutor(max_workers=2) as requests:
        replies = [future.result(timeout=5) for future in [requests.submit(resume), requests.submit(resume)]]
    assert all(reply.status_code == 200 for reply in replies)
    assert job.attempt == 2 and job.status == "queued"
    dispatch(job)
    assert calls == [2] and not job.resource_lease


@pytest.mark.parametrize("action", ["resume", "retry_unknown"])
def test_terminal_metadata_cannot_start_new_attempt_while_old_worker_owns_lease(r0_runtime, action):
    p = store.save(Project())
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump())
    entered, release = threading.Event(), threading.Event()

    def worker(current):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("R2 lease barrier timed out")

    jobs.launch(job, worker)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert entered.wait(5)
    with job._state_lock:
        job.status = "error" if action == "resume" else "blocked"
        job.blocked_reason = {"code": "PROVIDER_OUTCOME_UNKNOWN"}
        job.provider_intents = [{"id": "d" * 16, "status": "outcome_unknown"}]
    try:
        with pytest.raises(ValueError, match="chưa dừng"):
            if action == "resume":
                jobs.resume_render(job.id, job.state_version, job.config_fingerprint)
            else:
                jobs.authorize_unknown_retry(job.id, job.state_version)
        assert job.attempt == 1 and job.resource_lease
        assert job.provider_intents[0]["status"] == "outcome_unknown"
    finally:
        release.set()
    future.result(timeout=5)
    assert not job.resource_lease


@pytest.mark.parametrize("damage", ["assistant", "incomplete_upload", "missing_snapshot", "changed_snapshot", "foreign_snapshot"])
def test_recovery_does_not_advertise_start_without_valid_saved_input(r0_runtime, damage):
    p = store.save(Project())
    kind = "assistant" if damage == "assistant" else "upload" if damage == "incomplete_upload" else "plan"
    job = jobs.reserve(p.id, kind, payload={"files": [], "upload_incomplete": True} if kind == "upload" else {}, snapshot=p.model_dump())
    path = jobs.ROOT / job.snapshot_ref
    if damage == "missing_snapshot":
        path.unlink()
    elif damage == "changed_snapshot":
        store.atomic(path, {**p.model_dump(), "name": "Tampered"})
    elif damage == "foreign_snapshot":
        foreign = Project().model_dump()
        store.atomic(path, foreign)
        job.input_fingerprint = job_store.fingerprint({"kind": kind, "operation_version": job.operation_version, "payload": job.payload, "snapshot": foreign, "config_fingerprint": job.config_fingerprint})
    job.status = "queued"
    jobs._persist(job)
    jobs.initialize()
    detail = TestClient(main.app).get(f"/api/jobs/{job.id}").json()
    assert detail["can_resume"] is False
    assert detail["can_retry_unknown"] is False
    assert jobs.JOBS[job.id].resume_capability == "restart_only"


@pytest.mark.parametrize("kind,status", [("render", "running"), ("assistant", "running"), ("demo", "cancelling")])
def test_shutdown_refuses_to_relinquish_ownership_while_any_worker_can_write(r0_runtime, kind, status):
    p = store.save(Project())
    job = jobs.reserve(p.id, kind, snapshot=p.model_dump())
    entered, release = threading.Event(), threading.Event()

    def worker(current):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("R2 worker barrier timed out")
        (jobs.ROOT / "last-worker-write.txt").write_text("owned", encoding="utf-8")
        current.check()

    jobs.launch(job, worker)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert entered.wait(5)
    job.status = status
    try:
        assert jobs.stop_render_jobs(timeout=0.05) is False
        assert job.resource_lease and job.shutdown_requested
        with pytest.raises(ValueError):
            jobs.reserve(p.id, "render")
        with pytest.raises(RuntimeError, match="worker cũ"):
            jobs.initialize()
    finally:
        release.set()
    future.result(timeout=5)
    assert jobs.stop_render_jobs(timeout=1) is True
    assert job.status == "interrupted" and not job.resource_lease
    assert (r0_runtime / "last-worker-write.txt").read_text() == "owned"


def test_lifespan_retains_real_instance_lock_and_provider_unknown_until_worker_stops(r0_runtime, monkeypatch):
    loop = asyncio.new_event_loop()
    lifetime = main.lifespan(main.app)
    loop.run_until_complete(lifetime.__aenter__())
    p = store.save(Project())
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump())
    entered, release = threading.Event(), threading.Event()
    calls = []
    original_stop = jobs.stop_render_jobs

    def worker(current):
        call_id, _ = current.begin_provider_call("r2-inflight", "fake", "fake", "ai-json")
        calls.append(call_id)
        entered.set()
        if not release.wait(5):
            raise RuntimeError("R2 provider barrier timed out")
        current.finish_provider_call(call_id, "completed_cancelled", 200)
        current.check()

    jobs.launch(job, worker)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert entered.wait(5)
    monkeypatch.setattr(jobs, "stop_render_jobs", lambda: original_stop(timeout=0.05))
    try:
        with pytest.raises(RuntimeError, match="giữ khóa instance"):
            loop.run_until_complete(lifetime.__aexit__(None, None, None))
        env = dict(os.environ, CLIPFORGE_DATA=str(r0_runtime))
        blocked = subprocess.run([sys.executable, "-c", "from backend import store\ntry: store.acquire_instance_lock()\nexcept RuntimeError: print('blocked'); raise SystemExit(0)\nraise SystemExit(2)"], cwd=REPO, env=env, capture_output=True, text=True, timeout=5)
        assert blocked.returncode == 0 and blocked.stdout.strip() == "blocked"
        assert job.provider_intents[0]["status"] == "outcome_unknown"
        assert job.resource_lease
    finally:
        release.set()
        future.result(timeout=5)
        assert original_stop(timeout=1)
        store.release_instance_lock()
        loop.close()
    assert job.status == "blocked"
    jobs.initialize()
    recovered = jobs.JOBS[job.id]
    assert recovered.status == "blocked" and len(calls) == 1
    detail = TestClient(main.app).get(f"/api/jobs/{job.id}").json()
    assert not detail["can_resume"] and detail["can_retry_unknown"]


def test_shutdown_during_real_provider_routing_never_retries_or_applies_late_response(r0_runtime, monkeypatch):
    p = store.save(Project(name="Unchanged"))
    profile = AIProfile(name="Local R2 fixture", provider="compatible", model="fixture", base_url="http://127.0.0.1:9999/v1")
    monkeypatch.setattr(providers, "settings", lambda: Settings(ai_profiles=[profile], active_ai_profile_id=profile.id))
    entered, release = threading.Event(), threading.Event()
    requests, closed = [], []

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            closed.append(True)

        def post(self, url, **kwargs):
            requests.append(url)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("R2 HTTP response barrier timed out")
            return providers.httpx.Response(200, json={"choices": [{"message": {"content": '{"name":"Late result"}'}}]})

    monkeypatch.setattr(providers.httpx, "Client", Client)
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump())

    def worker(current):
        result = providers.ai_json("Fixture only", job=current)
        changed = store.read(p.id)
        changed.name = result["name"]
        current.commit(lambda: store.save(changed), p.id)

    jobs.launch(job, worker)
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert entered.wait(5)
    try:
        assert not jobs.stop_render_jobs(timeout=0.05)
        assert job.provider_intents[0]["status"] == "outcome_unknown"
    finally:
        release.set()
    future.result(timeout=5)
    assert jobs.stop_render_jobs(timeout=1)
    assert job.status == "blocked"
    assert job.provider_intents[0]["status"] == "outcome_unknown"
    assert store.read(p.id).name == "Unchanged" and store.read(p.id).revision == p.revision
    assert len(requests) == len(closed) == 1


def _process_kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in [("OpenProcess", [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong], ctypes.c_void_p),
        ("WaitForSingleObject", [ctypes.c_void_p, ctypes.c_ulong], ctypes.c_ulong),
        ("TerminateProcess", [ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
        ("CloseHandle", [ctypes.c_void_p], ctypes.c_int),
        ("GetProcessHandleCount", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)], ctypes.c_int),
        ("GetCurrentProcess", [], ctypes.c_void_p)]:
        method = getattr(kernel, name)
        method.argtypes, method.restype = args, result
    return kernel


def _handle_count(kernel):
    count = ctypes.c_ulong()
    assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
    return count.value


@pytest.mark.skipif(os.name != "nt", reason="Native Windows ownership")
def test_killed_backend_lifespan_has_no_encoder_or_writer_after_restart(r0_runtime, monkeypatch):
    data = r0_runtime / "actual-backend"
    data.mkdir()
    env = dict(os.environ, CLIPFORGE_DATA=str(data))
    handles = []
    kernel = _process_kernel()
    with (r0_runtime / "backend-stderr.txt").open("wb") as err:
        owner = subprocess.Popen([sys.executable, "-u", "-m", "tests.fixtures.reliability.backend_process_owner"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            identity_file = data / "process-identity.json"
            deadline = time.monotonic() + 12
            while not identity_file.exists() and owner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            assert identity_file.exists(), (r0_runtime / "backend-stderr.txt").read_text()
            identity = json.loads(identity_file.read_text())
            with urlopen(f"http://127.0.0.1:{identity['port']}/api/health", timeout=3) as response:
                assert response.status == 200
            with urlopen(f"http://127.0.0.1:{identity['port']}/api/jobs/{identity['job_id']}", timeout=3) as response:
                assert json.load(response)["status"] == "running"
            for pid in (identity["owner_pid"], identity["ffmpeg_pid"]):
                handle = kernel.OpenProcess(0x100000 | 0x0001, False, pid)
                assert handle
                handles.append(handle)
            assert kernel.WaitForSingleObject(handles[1], 300) == 258
            assert kernel.TerminateProcess(handles[0], 1)
            assert kernel.WaitForSingleObject(handles[0], 5000) == 0
            assert kernel.WaitForSingleObject(handles[1], 3000) == 0
            owner.wait(timeout=5)
            successor = subprocess.run([sys.executable, "-c", "from backend import store; store.acquire_instance_lock(); print('owned'); store.release_instance_lock()"], cwd=REPO, env=env, capture_output=True, text=True, timeout=5)
            assert successor.returncode == 0 and successor.stdout.strip() == "owned"
            monkeypatch.setattr(jobs, "ROOT", data)
            monkeypatch.setattr(store, "ROOT", data)
            jobs.initialize()
            recovered = jobs.JOBS[identity["job_id"]]
            assert recovered.status == "interrupted" and recovered.resume_capability == "snapshot"
            assert not recovered.dispatch_ready or recovered.status != "queued"
            assert store.read(identity["project_id"]).exports == []
        finally:
            for handle in handles:
                if kernel.WaitForSingleObject(handle, 0) == 258:
                    kernel.TerminateProcess(handle, 1)
                    kernel.WaitForSingleObject(handle, 5000)
                kernel.CloseHandle(handle)
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Objects")
@pytest.mark.parametrize("fault", ["configure", "assign", "resume", "spawn"])
def test_native_creation_errors_close_job_thread_and_process_handles(r0_runtime, monkeypatch, fault):
    processes.ensure_owner()
    kernel = _process_kernel()
    before = _handle_count(kernel)
    original_kernel = processes._kernel
    created = []
    actual_popen = subprocess.Popen

    class FaultKernel:
        def __init__(self):
            self.actual = original_kernel()

        def __getattr__(self, name):
            if name == {"configure": "SetInformationJobObject", "assign": "AssignProcessToJobObject", "resume": "ResumeThread"}.get(fault):
                def fail(*_):
                    raise OSError("R2 native fault " + fault)
                return fail
            return getattr(self.actual, name)

    def popen(*args, **kwargs):
        if fault == "spawn":
            raise OSError("R2 native fault spawn")
        process = actual_popen(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(processes, "_kernel", FaultKernel)
    with pytest.raises(OSError, match="R2 native fault"):
        with processes.command([media.ffmpeg(), "-version"], popen_factory=popen, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
            pytest.fail("Faulted command must never run")
    assert all(process.returncode is not None for process in created)
    assert processes.active_count() == 0
    gc.collect()
    assert _handle_count(kernel) <= before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows handle accounting")
def test_repeated_commands_do_not_leak_native_handles(r0_runtime):
    processes.ensure_owner()
    kernel = _process_kernel()
    before = _handle_count(kernel)
    for _ in range(12):
        result = processes.run([media.ffmpeg(), "-version"], timeout=5, capture_output=True)
        assert result.returncode == 0
        with pytest.raises(FileNotFoundError):
            processes.run([str(r0_runtime / "missing.exe")], timeout=5)
    gc.collect()
    assert processes.active_count() == 0 and _handle_count(kernel) <= before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows command trees")
@pytest.mark.parametrize("leader_exits", [False, True])
def test_command_scope_terminates_helpers_including_an_exited_leaders_child(r0_runtime, leader_exits):
    identity = r0_runtime / "helper.json"
    code = f"""
import json, os, subprocess, time
from pathlib import Path
from backend import media, processes
if not {leader_exits!r}: processes.ensure_owner()
child = subprocess.Popen([media.ffmpeg(), '-nostdin', '-re', '-f', 'lavfi', '-i', 'color=c=blue:size=64x64:rate=1', '-t', '30', '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
Path({str(identity)!r}).write_text(json.dumps({{'pid':child.pid}}))
if not {leader_exits!r}: time.sleep(30)
"""
    kernel = _process_kernel()
    handle = None
    try:
        with processes.command([sys.executable, "-u", "-c", code], cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as leader:
            deadline = time.monotonic() + 10
            while not identity.exists() and leader.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            assert identity.exists()
            handle = kernel.OpenProcess(0x100000 | 0x0001, False, json.loads(identity.read_text())["pid"])
            assert handle and kernel.WaitForSingleObject(handle, 150) == 258
            if leader_exits:
                leader.wait(timeout=5)
                assert kernel.WaitForSingleObject(handle, 100) == 258
        assert kernel.WaitForSingleObject(handle, 3000) == 0
        assert processes.active_count() == 0
    finally:
        if handle:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 5000)
            kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows encoder cancellation")
def test_cancel_running_encoder_uses_graceful_quit_and_releases_native_handles(r0_runtime, monkeypatch):
    p = store.save(Project())
    job = jobs.reserve(p.id, "render", snapshot=p.model_dump())
    output = store.project_dir(p.id) / "exports" / job.id / "video.mp4"
    output.parent.mkdir(parents=True)
    started = threading.Event()
    spawned = []
    original = subprocess.Popen

    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        spawned.append(process)
        started.set()
        return process

    monkeypatch.setattr(media.subprocess, "Popen", tracked)
    jobs.launch(job, lambda current: media.run_ff(["-re", "-f", "lavfi", "-i", "color=c=blue:size=64x64:rate=5", "-t", "30", "-c:v", "libx264", output], job=current, timeout=40))
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert started.wait(5)
    # Give the real encoder time to open its owned output before requesting q.
    deadline = time.monotonic() + 5
    while not output.exists() and time.monotonic() < deadline:
        time.sleep(0.025)
    assert output.exists()
    jobs.cancel(job.id)
    future.result(timeout=5)
    assert job.status == "cancelled" and not job.resource_lease
    assert not output.exists()
    assert spawned[0].returncode == 0  # q let FFmpeg finish its mux before cleanup.
    assert processes.active_count() == 0


@pytest.mark.skipif(os.name != "nt", reason="Native cancellable TTS/capability runner")
def test_cancellable_native_runner_stops_before_late_output_and_joins_pipe_readers(r0_runtime):
    identity = r0_runtime / "native-pid.txt"
    late = r0_runtime / "late-output.txt"
    code = f"import os,time; from pathlib import Path; Path({str(identity)!r}).write_text(str(os.getpid())); print('ready',flush=True); time.sleep(30); Path({str(late)!r}).write_text('late')"
    job = jobs.Job(Project().id, "voice")
    kernel = _process_kernel()
    handle = None
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(processes.run, [sys.executable, "-u", "-c", code], timeout=35, capture_output=True, job=job)
        try:
            deadline = time.monotonic() + 8
            while not identity.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(0.025)
            assert identity.exists()
            handle = kernel.OpenProcess(0x100000 | 0x0001, False, int(identity.read_text()))
            assert handle
            job.cancelled.set()
            with pytest.raises(jobs.Cancelled):
                future.result(timeout=5)
            assert kernel.WaitForSingleObject(handle, 1000) == 0
            assert not late.exists() and processes.active_count() == 0
        finally:
            job.cancelled.set()
            if handle:
                if kernel.WaitForSingleObject(handle, 0) == 258:
                    kernel.TerminateProcess(handle, 1)
                    kernel.WaitForSingleObject(handle, 5000)
                kernel.CloseHandle(handle)
