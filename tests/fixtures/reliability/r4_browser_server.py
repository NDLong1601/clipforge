"""Test-only HTTP fixture over the real ClipForge API; never use the user's root."""

import os
from pathlib import Path
import threading

from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

if not Path(os.environ['CLIPFORGE_DATA']).name.startswith('clipforge-r4-browser-'):
    raise RuntimeError('Browser fixture requires its own temporary data directory')

from backend import jobs, main, media, store, recovery_paths, providers
from backend.errors import ProviderOutcomeUnknown
from backend.models import Project

# UI lifecycle uses tiny controlled encodes. Resource admission is tested in R5.
jobs._available_memory_bytes = lambda: 8 * 1024**3

app = main.app
app.router.routes = [route for route in app.router.routes if getattr(route, 'path', None) not in ('', '/')]
GATE = threading.Event()
GATE.set()


def fixture_render(job):
    if not GATE.wait(15):
        raise RuntimeError('Test did not release render gate')
    directory = store.project_dir(job.pid) / 'exports' / job.id
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / 'video.mp4'
    media.run_ff(['-f', 'lavfi', '-i', 'color=red:s=64x64:d=0.3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(output)], job=job)
    def commit():
        p = store.read(job.pid)
        p.exports.append({'id': job.id, 'filename': 'video.mp4', 'duration': 0.3, 'width': 64,
            'height': 64, 'project_revision': p.revision + 1})
        return store.save(p)
    saved = job.commit(commit, project_id=job.pid, allow_latest=True)
    return {'project_id': saved.id, 'revision': saved.revision}


def fixture_assemble(job):
    job.check()
    p = job.commit(lambda: store.save(store.read(job.pid)), project_id=job.pid)
    return {'project_id': p.id, 'revision': p.revision}


jobs._HANDLERS['render'] = fixture_render
jobs._HANDLERS['assemble-audio'] = fixture_assemble


def fixture_voice(job):
    call_id, cached = job.begin_provider_call('browser-tts', 'fixture', 'fixture', 'tts')
    if cached is not None:
        providers._restore_provider_audio(cached, store.project_dir(job.pid) / 'speech.wav', job, call_id)
    if not GATE.wait(15):
        raise RuntimeError('Test did not release TTS gate')
    # Mock the paid response; backend regression tests validate actual WAV restore.
    job.complete_provider_call(call_id, {'fixture': 'new TTS response'})
    def commit():
        p = store.read(job.pid)
        p.script = 'Recovered TTS result'
        return store.save(p)
    p = job.commit(commit, project_id=job.pid)
    return {'project_id': p.id, 'revision': p.revision}


jobs._HANDLERS['voice'] = fixture_voice


@app.post('/__test__/seed')
def seed(data: dict):
    GATE.set()
    for future in list(jobs._FUTURES.values()):
        future.result(timeout=20)
    with jobs.LOCK:
        jobs.JOBS.clear()
        jobs._FUTURES.clear()
        jobs._RUNTIME_HANDLERS.clear()
        for name in ('projects', 'jobs', 'job_runs', 'cache', 'staging'):
            recovery_paths.remove_tree(store.ROOT, store.ROOT / name)
        jobs._SCHEDULER_PAUSED = True
        jobs._save_scheduler_settings()
    p = store.save(Project(name='R4 browser A', script='Original script'))
    b = store.save(Project(name='R4 browser B', script='Other project'))
    kind = data.get('kind', 'render')
    job = jobs.reserve(p.id, kind, payload={}, snapshot=p.model_dump(), config_fingerprint=main._job_config_fingerprint(kind))
    job.status = job.phase = data.get('status', 'error')
    job.message = 'Fixture attempt 1 failed'
    job.resume_capability = 'snapshot'
    if kind == 'upload':
        job.result = {'auto_assemble': True, 'project_id': p.id, 'revision': p.revision}
    elif kind == 'voice':
        call_id, _ = job.begin_provider_call('browser-tts', 'fixture', 'fixture', 'tts')
        cached = {'artifact_path': 'missing-speech.wav', 'sha256': 'missing'}
        job.complete_provider_call(call_id, cached)
        try:
            providers._restore_provider_audio(cached, store.project_dir(p.id) / 'speech.wav', job, call_id)
        except ProviderOutcomeUnknown:
            job.status = job.phase = 'blocked'
    elif data.get('checkpoint'):
        cache = store.ROOT / 'cache' / 'shared.bin'; cache.parent.mkdir(parents=True); cache.write_bytes(b'temporary cache')
        original = store.project_dir(p.id) / 'assets' / 'source.bin'; original.parent.mkdir(parents=True); original.write_bytes(b'original source')
        incomplete = store.project_dir(p.id) / 'exports' / job.id / 'video.mp4'; incomplete.parent.mkdir(parents=True); incomplete.write_bytes(b'incomplete')
        job.save_checkpoint('fixture', 'fingerprint', {'ready': True})
        job.resume_capability = 'checkpointed'
    jobs._persist(job)
    if data.get('hold'): GATE.clear()
    return {'pid': p.id, 'other': b.id, 'jid': job.id}


@app.post('/__test__/release')
def release():
    GATE.set()
    return {'released': True}


@app.post('/__test__/complete/{jid}')
def complete(jid: str):
    job = jobs.JOBS[jid]
    result = fixture_render(job)
    job.result = result
    job.status = job.phase = 'done'
    job.attempt = 2
    jobs._persist(job)
    return job.dump()


@app.get('/__test__/facts')
def facts():
    return {'jobs': [{'id': j.id, 'kind': j.kind, 'status': j.status, 'attempt': j.attempt, 'payload': j.payload} for j in jobs.JOBS.values()],
        'pins': {j.pid: jobs.has_pinned_artifacts(j.pid) for j in jobs.JOBS.values()},
        'sources': [p.read_text() for p in (store.ROOT / 'projects').glob('*/assets/source.bin')]}


@app.post('/__test__/crash')
def crash():
    return JSONResponse({'crashing': True}, background=BackgroundTask(lambda: os._exit(91)))


app.mount('/', StaticFiles(directory=os.environ['CLIPFORGE_TEST_DIST'], html=True), name='r4-browser-ui')
