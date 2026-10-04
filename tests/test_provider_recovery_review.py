"""Regression coverage for RR1/RR2 using real dispatch and mock paid APIs."""
from concurrent.futures import ThreadPoolExecutor
import errno
import io
import wave

import httpx
import pytest

from backend import job_store, jobs, providers, store
from backend.models import Project


@pytest.fixture
def runtime(r0_runtime, monkeypatch):
    monkeypatch.setattr(providers, "ROOT", r0_runtime)
    monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 30 * 1024**3)
    monkeypatch.setattr(jobs, "_volume_state", lambda: {
        "volume": {"free": 30 * 1024**3, "labels": ["data", "temp"]}})
    store.save_settings({"tts_provider": "openai", "azure_key": "mock", "azure_region": "eastus",
        "ai_profiles": [{"id": "fixture", "name": "Fixture", "provider": "openai",
            "api_key": "mock", "model": "fixture", "enabled": True}], "active_ai_profile_id": "fixture"})
    return r0_runtime


def dispatch(job):
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert future is not None
    future.result(timeout=15)


def resume(job):
    jobs.resume_render(job.id, job.state_version, job.config_fingerprint)
    dispatch(job)


def mock_http(monkeypatch, respond):
    real_client = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", lambda **kw:
        real_client(transport=httpx.MockTransport(respond), **kw))


@pytest.mark.parametrize("resource", ["ram", "disk"])
def test_paid_json_survives_pressure_after_response(runtime, monkeypatch, resource):
    p = store.save(Project())
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump())
    pressure = [False]
    requests = []
    if resource == "ram":
        monkeypatch.setattr(jobs, "_available_memory_bytes", lambda: 1 if pressure[0] else 30 * 1024**3)
    else:
        monkeypatch.setattr(jobs, "_volume_state", lambda: {
            "volume": {"free": 1 if pressure[0] else 30 * 1024**3, "labels": ["data", "temp"]}})

    def respond(request):
        requests.append(request)
        pressure[0] = True
        job._last_resource_check = 0
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    mock_http(monkeypatch, respond)
    jobs.register_handler("plan", lambda current: providers.ai_json("fixture", job=current))
    jobs.launch(job)
    dispatch(job)
    assert job.status == "interrupted" and job.phase == "resource_wait"
    assert job.provider_intents[0]["status"] == "success"
    assert job.result is None and store.read(p.id).revision == p.revision
    pressure[0] = False
    resume(job)
    assert job.status == "done" and job.result == {"ok": True}
    assert len(requests) == 1


def test_provider_result_write_failure_requires_authorization(runtime, monkeypatch):
    p = store.save(Project())
    job = jobs.reserve(p.id, "plan", snapshot=p.model_dump())
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    mock_http(monkeypatch, respond)
    jobs.register_handler("plan", lambda current: providers.ai_json("fixture", job=current))
    original = job_store.write_provider_result

    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "fixture")

    monkeypatch.setattr(job_store, "write_provider_result", no_space)
    jobs.launch(job)
    dispatch(job)
    assert job.status == "error"
    monkeypatch.setattr(job_store, "write_provider_result", original)
    resume(job)
    assert job.status == "blocked" and len(requests) == 1
    assert job.provider_intents[0]["status"] == "outcome_unknown"
    jobs.authorize_unknown_retry(job.id, job.state_version)
    resume(job)
    assert job.status == "done" and len(requests) == 2


@pytest.mark.parametrize("provider", ["openai", "azure"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
@pytest.mark.parametrize("restart", [False, True])
def test_tts_damaged_cache_can_be_authorized_once(runtime, monkeypatch, provider, damage, restart):
    store.save_settings({"tts_provider": provider})
    p = store.save(Project())
    job = jobs.reserve(p.id, "voice", snapshot=p.model_dump())
    dest = runtime / "speech.wav"
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 2400)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=audio.getvalue())

    def handler(current):
        providers.speak_sentence("fixture", dest, providers.effective_settings(current), current)
        if current.attempt == 1:
            raise jobs.Interrupted()
        return {"ok": True}

    mock_http(monkeypatch, respond)
    jobs.register_handler("voice", handler)
    jobs.launch(job)
    dispatch(job)
    assert job.status == "interrupted" and dest.is_file()
    if damage == "missing":
        dest.unlink()
    else:
        dest.write_bytes(b"changed")
    resume(job)
    assert job.status == "blocked" and job.provider_intents[0]["status"] == "outcome_unknown"
    assert len(requests) == 1
    if restart:
        jobs.initialize()
        job = jobs.JOBS[job.id]
    with pytest.raises(ValueError):
        jobs.authorize_unknown_retry(job.id, job.state_version - 1)
    version = job.state_version

    def authorize():
        try:
            jobs.authorize_unknown_retry(job.id, version)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: authorize(), range(2)))
    assert sorted(results) == [False, True]
    resume(job)
    assert job.status == "done" and len(requests) == 2
    assert dest.read_bytes() == audio.getvalue()
