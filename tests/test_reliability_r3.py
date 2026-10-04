"""R3 exercises admission, provider routing and media caches in an isolated store."""

from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import wave

import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend import job_inputs, job_store, jobs, main, media, providers, render, store
from backend.errors import InputBlocked
from backend.models import AIProfile, Asset, Clip, Cue, Project, Scene, Settings

pytestmark = pytest.mark.reliability_r3


def dispatch(job):
    with jobs.LOCK:
        jobs._dispatch_one_locked(job)
        future = job._future
    assert future is not None
    future.result(timeout=20)


def config():
    settings = Settings(ai_profiles=[AIProfile(id='route', name='Private route', provider='compatible',
        base_url='https://first.example/v1', model='model-a', api_key='secret-first')], active_ai_profile_id='route')
    store.save_settings(settings.model_dump())
    return settings


def saved_job(kind='plan', project=None, payload=None):
    project = project or store.save(Project(script='Saved script.'))
    return project, jobs.reserve(project.id, kind, payload=payload or {}, snapshot=project.model_dump(),
        config_fingerprint=main._job_config_fingerprint(kind))


def edit(project):
    latest = store.read(project.id)
    latest.script = 'New script.'
    return store.save(latest)


def wave_bytes(frequency=440, seconds=5):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b''.join(struct.pack('<h', round(5000 * math.sin(2 * math.pi * frequency * i / 48000)))
            for i in range(round(48000 * seconds))))
    return output.getvalue()


def image_project(voice=False):
    p = Project(name='R3 native render', target_duration=5, resolution='720', auto_sound_effects=False)
    p.template.caption.enabled = False
    directory = store.project_dir(p.id) / 'assets'
    directory.mkdir(parents=True)
    Image.new('RGB', (64, 64), 'red').save(directory / 'source.bmp')
    asset = Asset(name='Source', filename='source.bmp', media='image', width=64, height=64)
    p.assets = [asset]
    p.clips = [Clip(asset_id=asset.id, duration=5)]
    if voice:
        (directory / 'voice.wav').write_bytes(wave_bytes())
        audio = Asset(name='Voice', filename='voice.wav', role='voice', **media.probe(directory / 'voice.wav'))
        p.assets.append(audio)
        p.voice_id = audio.id
    return store.save(p)


@pytest.mark.parametrize('kind', ['analyze', 'plan', 'voice', 'template', 'transcribe', 'assemble-audio', 'filter-faces'])
@pytest.mark.parametrize('stage', ['before_launch', 'before_lease'])
def test_revision_guard_covers_all_mutating_jobs_before_any_handler(r0_runtime, monkeypatch, kind, stage):
    project, job = saved_job(kind)
    calls = []
    monkeypatch.setitem(jobs._HANDLERS, kind, lambda current: calls.append(current.id))
    if stage == 'before_lease':
        jobs.launch(job)
    edit(project)
    if stage == 'before_launch':
        result = jobs.launch(job)
        assert result['status'] == 'blocked'
        assert not job.dispatch_ready and job._future is None
    else:
        dispatch(job)
    assert calls == []
    assert job.status == 'blocked' and job.error['code'] == 'REVISION_CONFLICT'
    assert job.attempt == 1 and not job.resource_lease
    assert store.read(project.id).script == 'New script.'


@pytest.mark.parametrize('operation', ['analyze', 'plan', 'transcribe', 'template'])
def test_stale_explicit_submission_revision_does_not_create_a_job(r0_runtime, operation):
    p = store.save(Project())
    edit(p)
    response = TestClient(main.app).post(f'/api/projects/{p.id}/{operation}', json={'revision': p.revision})
    assert response.status_code == 409 and jobs.JOBS == {}


@pytest.mark.parametrize('kind,change', [('voice', 'endpoint'), ('plan', 'model'), ('analyze', 'analysis_version'), ('render', 'render_version')])
@pytest.mark.parametrize('stage', ['before_launch', 'before_lease'])
def test_queued_config_change_blocks_first_attempt(r0_runtime, monkeypatch, kind, change, stage):
    settings = config()
    _, job = saved_job(kind)
    calls = []
    monkeypatch.setitem(jobs._HANDLERS, kind, lambda current: calls.append(current.id))
    if stage == 'before_lease':
        jobs.launch(job)
    if change == 'endpoint':
        settings.ai_profiles[0].base_url = 'https://second.example/v1'
        store.save_settings(settings.model_dump())
    elif change == 'model':
        settings.ai_profiles[0].model = 'model-b'
        store.save_settings(settings.model_dump())
    elif change == 'analysis_version':
        monkeypatch.setattr(media, 'ANALYSIS_VERSION', 'new-analysis-version')
    else:
        monkeypatch.setattr(render, 'RENDER_VERSION', 'new-render-version')
    if stage == 'before_launch':
        jobs.launch(job)
    else:
        dispatch(job)
    assert calls == [] and job.status == 'blocked'
    assert job.error['code'] == 'CONFIG_CHANGED' and not job.resource_lease


@pytest.mark.parametrize('kind', ['analyze', 'plan', 'voice', 'transcribe', 'assemble-audio'])
def test_credentials_do_not_change_computation_identity_or_enter_job_files(r0_runtime, kind):
    settings = config()
    original = job_inputs.config_fingerprint(kind, settings)
    settings.ai_profiles[0].api_key = 'secret-rotated'
    settings.azure_key = 'secret-azure'
    settings.ai_key = 'secret-legacy'
    assert job_inputs.config_fingerprint(kind, settings) == original
    public_identity = json.dumps(job_inputs.config_identity(kind, settings))
    assert 'secret-' not in public_identity and 'https://' not in public_identity
    settings.ai_profiles[0].base_url = 'https://another.example/v1'
    assert job_inputs.config_fingerprint(kind, settings) != original


def test_endpoint_identity_excludes_url_credentials_but_distinguishes_routing(r0_runtime):
    assert job_inputs.endpoint_id('https://user:secret@EXAMPLE.com/v1/?api_key=one&tenant=A#secret') == job_inputs.endpoint_id('https://example.com/v1?api_key=two&tenant=A')
    assert job_inputs.endpoint_id('https://example.com/v1?tenant=A') != job_inputs.endpoint_id('https://example.com/v1?tenant=B')


def test_resume_conflict_preserves_checkpoint_and_offers_one_current_revision_replacement(r0_runtime, monkeypatch):
    p, job = saved_job('plan', payload={'use_ai': True})
    job.save_checkpoint('prior', 'fixture', {'kept': True})
    job.status = 'error'
    jobs._persist(job)
    changed = edit(p)
    response = TestClient(main.app).post(f'/api/jobs/{job.id}/resume', json={'state_version': job.state_version})
    assert response.status_code == 409 and job.attempt == 1
    detail = TestClient(main.app).get(f'/api/jobs/{job.id}').json()
    assert detail['status'] == 'blocked' and detail['can_restart_current'] and not detail['can_resume']
    client = TestClient(main.app)
    assert client.post(f'/api/jobs/{job.id}/restart-current', json={'state_version': detail['state_version'] - 1}).status_code == 409
    def start():
        return TestClient(main.app).post(f'/api/jobs/{job.id}/restart-current', json={'state_version': detail['state_version']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: start(), range(2)))
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    replacement = jobs.JOBS[next(reply.json()['id'] for reply in replies if reply.status_code == 200)]
    assert len(jobs.JOBS) == 2 and replacement.parent_job_id == job.id
    assert replacement.input_revision == changed.revision and replacement.id != job.id and replacement.attempt == 1
    assert job.load_checkpoint('prior', 'fixture') == {'kept': True}
    calls = []
    monkeypatch.setitem(jobs._HANDLERS, 'plan', lambda current: calls.append(job_store.load_snapshot(jobs.ROOT, current.snapshot_ref)['script']))
    dispatch(replacement)
    assert calls == ['New script.']
    assert start().status_code == 409 and len(jobs.JOBS) == 2
    latest = client.get(f'/api/jobs/{job.id}').json()
    assert latest['state_version'] > detail['state_version'] and not latest['can_restart_current']
    assert client.post(f'/api/jobs/{job.id}/restart-current', json={'state_version': latest['state_version']}).status_code == 409
    jobs.initialize()
    reloaded = jobs.JOBS[job.id]
    assert reloaded.result == {'replacement_job_id': replacement.id}
    assert not client.get(f'/api/jobs/{job.id}').json()['can_restart_current']


def test_render_snapshot_revision_is_an_intentional_exception(r0_runtime, monkeypatch):
    p, job = saved_job('render')
    edit(p)
    calls = []
    monkeypatch.setitem(jobs._HANDLERS, 'render', lambda current: calls.append(current.input_revision))
    jobs.launch(job)
    dispatch(job)
    assert job.status == 'done' and calls == [p.revision]


def test_running_tts_freezes_effective_route_and_never_serializes_credentials(r0_runtime, monkeypatch):
    settings = config()
    p = store.save(Project(script='First sentence. Second sentence.', target_duration=5))
    _, job = saved_job('voice', p)
    monkeypatch.setattr(providers, 'ROOT', r0_runtime)
    requests = []
    audio = wave_bytes(seconds=0.5)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            requests.append((url, kwargs['headers']['Authorization']))
            settings.ai_profiles[0].base_url = 'https://second.example/v1'
            settings.ai_profiles[0].api_key = 'secret-rotated'
            store.save_settings(settings.model_dump())
            return httpx.Response(200, content=audio)
    monkeypatch.setattr(providers.httpx, 'Client', Client)
    jobs.launch(job)
    dispatch(job)
    assert job.status == 'done'
    assert requests == [('https://first.example/v1/audio/speech', 'Bearer secret-first')] * 2
    record = job_store.job_path(r0_runtime, job.id).read_text(encoding='utf-8')
    assert 'secret-' not in record and '_settings' not in record and 'https://' not in record
    assert store.read(p.id).voice_id


def test_commit_still_rejects_edit_after_provider_started(r0_runtime, monkeypatch):
    p, job = saved_job('voice')
    calls = []
    def synthesize(snapshot, current):
        calls.append(snapshot.script)
        edit(p)
        return snapshot
    monkeypatch.setattr(providers, 'synthesize', synthesize)
    jobs.launch(job)
    dispatch(job)
    assert calls == ['Saved script.']
    assert job.error['code'] == 'REVISION_CONFLICT' and not job.resource_lease
    assert store.read(p.id).script == 'New script.' and not job._commit_done


@pytest.mark.parametrize('damage', ['none', 'schema', 'foreign_project', 'broken_refs', 'input_media', 'output_media'])
def test_project_result_cache_validates_schema_project_and_real_media(r0_runtime, monkeypatch, damage):
    p = image_project()
    _, job = saved_job('plan', p)
    calls = []
    monkeypatch.setattr(main.planner, 'plan', lambda project, use_ai, current: (calls.append(project.script), project)[1])
    monkeypatch.setattr(job, 'commit', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('fixture: crash before commit')))
    with pytest.raises(ValueError, match='fixture:'):
        main._run_project_job(job)
    manifest_path = job_store.pipeline_path(r0_runtime, job.id)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    entry = manifest['steps']['project-result']['value']
    if damage == 'schema': entry['schema_version'] = 999
    if damage == 'foreign_project': entry['project']['id'] = 'f' * 16
    if damage == 'broken_refs': entry['project']['clips'][0]['asset_id'] = 'f' * 16
    if damage == 'output_media': entry['media'][0]['sha256'] = '0' * 64
    if damage == 'input_media': Image.new('RGB', (64, 64), 'blue').save(store.asset_path(p.id, p.assets[0]))
    store.atomic(manifest_path, manifest)
    with pytest.raises(ValueError, match='fixture:'):
        main._run_project_job(job)
    assert len(calls) == (1 if damage == 'none' else 2)
    assert store.read(p.id).revision == p.revision


@pytest.mark.parametrize('damage', ['none', 'schema', 'out_of_range', 'unordered', 'foreign_source', 'blank', 'key_rotation', 'endpoint'])
def test_transcription_checkpoint_is_a_validated_content_cache(r0_runtime, monkeypatch, damage):
    settings = config()
    settings.transcription_provider = 'gemini'
    store.save_settings(settings.model_dump())
    p = image_project(voice=True)
    _, job = saved_job('transcribe', p)
    asset = store.get_asset(p, p.voice_id)
    calls = []
    monkeypatch.setattr(providers, '_gemini_audio', lambda *args: (calls.append(1), [Cue(start=0, end=1, text='Validated words')])[1])
    providers.transcribe(p.model_copy(deep=True), asset, job)
    path = job_store.pipeline_path(r0_runtime, job.id)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    value = manifest['steps']['transcription']['value']
    if damage == 'schema': value['schema_version'] = 999
    if damage == 'out_of_range': value['cues'][0]['end'] = asset.duration + 1
    if damage == 'unordered': value['cues'] = [{'start': 2, 'end': 3, 'text': 'Later'}, {'start': 0, 'end': 1, 'text': 'Earlier'}]
    if damage == 'blank': value['cues'][0]['text'] = ' '
    if damage == 'foreign_source': store.asset_path(p.id, asset).write_bytes(wave_bytes(880))
    if damage == 'key_rotation':
        settings.ai_profiles[0].api_key = 'secret-rotated'
        store.save_settings(settings.model_dump())
    if damage == 'endpoint':
        settings.ai_profiles[0].base_url = 'https://second.example/v1'
        store.save_settings(settings.model_dump())
    store.atomic(path, manifest)
    restored = providers.transcribe(p.model_copy(deep=True), asset, job)
    assert len(calls) == (1 if damage in {'none', 'key_rotation'} else 2)
    assert restored.script == 'Validated words'


def test_invalid_known_provider_result_is_blocked_without_a_paid_retry(r0_runtime, monkeypatch):
    config()
    p = image_project(voice=True)
    _, job = saved_job('transcribe', p)
    asset = store.get_asset(p, p.voice_id)
    monkeypatch.setattr(job, 'begin_provider_call', lambda *args: (None, {'cues': [{'start': 0, 'end': 999, 'text': 'Invalid'}]}))
    monkeypatch.setattr(providers.httpx, 'Client', lambda *args, **kwargs: pytest.fail('Must not send a paid retry'))
    with pytest.raises(InputBlocked, match='không tự gửi lại'):
        providers.transcribe(p, asset, job)


@pytest.mark.parametrize('damage', ['none', 'schema', 'foreign_asset', 'wrong_hash', 'range', 'overlap', 'path'])
def test_scene_analysis_cache_validates_content_identity_and_scene_schema(r0_runtime, monkeypatch, damage):
    p = Project()
    directory = store.project_dir(p.id) / 'assets'
    directory.mkdir(parents=True)
    source = directory / 'source.mp4'
    media.run_ff(['-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=30', '-t', '1', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', source])
    asset = Asset(name='Scene source', filename=source.name, **media.probe(source))
    p.assets = [asset]
    store.save(p)
    media.analyze(p.id, asset)
    ids = [scene.id for scene in asset.scenes]
    path = media._analysis_cache_path(asset, source)
    value = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'schema': value['schema_version'] = 999
    if damage == 'foreign_asset': value['asset_id'] = 'f' * 16
    if damage == 'wrong_hash': value['source_sha256'] = '0' * 64
    if damage == 'range': value['scenes'][0]['end'] = asset.duration + 1
    if damage == 'overlap': value['scenes'] = [value['scenes'][0], {**value['scenes'][0], 'id': 'f' * 16}]
    if damage == 'path': value['scenes'][0]['thumbnail'] = '../outside.jpg'
    store.atomic(path, value)
    opens = []
    actual_open = media.av.open
    monkeypatch.setattr(media.av, 'open', lambda *args, **kwargs: (opens.append(1), actual_open(*args, **kwargs))[1])
    media.analyze(p.id, asset)
    if damage == 'none':
        assert opens == [] and [scene.id for scene in asset.scenes] == ids
    else:
        assert opens and [scene.id for scene in asset.scenes] != ids
        assert media._cached_scenes(p.id, asset, path) is not None


def test_content_hash_detects_same_size_and_timestamp_replacement(r0_runtime):
    path = r0_runtime / 'same-stat.bin'
    path.write_bytes(b'original')
    stat = path.stat()
    before = media.file_sha256(path)
    path.write_bytes(b'replaced')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert media.file_sha256(path) != before and render._file_sha256(path) != before


@pytest.mark.parametrize('dependency', ['none', 'source_same_stat', 'voice', 'font', 'encoder_flags'])
def test_completed_native_mux_recomputes_dependencies_before_shortcut(r0_runtime, monkeypatch, dependency):
    p = image_project(voice=True)
    if dependency == 'font':
        original_path, actual_family, found = render.resolve('Arial')
        assert original_path is not None
        copied = r0_runtime / 'font.ttf'
        shutil.copy2(original_path, copied)
        monkeypatch.setattr(render, 'resolve', lambda family, bold=False: (copied, actual_family, found))
    _, job = saved_job('render', p, {'preview': True})
    result = render.render(p, job, True)
    output = store.project_dir(p.id) / 'exports' / job.id / 'preview.mp4'
    original_hash = media.file_sha256(output)
    if dependency == 'source_same_stat':
        source = store.asset_path(p.id, p.assets[0])
        stat = source.stat()
        Image.new('RGB', (64, 64), 'blue').save(source)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert source.stat().st_size == stat.st_size
    if dependency == 'voice': store.asset_path(p.id, store.get_asset(p, p.voice_id)).write_bytes(wave_bytes(880))
    if dependency == 'font': copied.write_bytes(copied.read_bytes() + b'R3-font-replacement')
    if dependency == 'encoder_flags':
        options = render._video_encode_options
        monkeypatch.setattr(render, '_video_encode_options', lambda preview, encoder: [*options(preview, encoder), '-metadata', 'comment=R3'])
    calls = []
    run = render.run_ff
    monkeypatch.setattr(render, 'run_ff', lambda *args, **kwargs: (calls.append(args), run(*args, **kwargs))[1])
    if dependency != 'none':
        assert 'mux' in render.resume_plan(job)['missing_or_changed']
    restored = render.render(p, job, True)
    assert bool(calls) == (dependency != 'none')
    if dependency == 'none': assert restored == result and media.file_sha256(output) == original_hash
    if dependency in {'source_same_stat', 'voice'}: assert media.file_sha256(output) != original_hash
    assert restored['clock_verified']
    render._validate_video_clock(output, 5)
    render._validate_audio_clock(output, 5, restored['audio_encoder'])


def test_partial_resume_reuses_clips_while_changed_voice_rebuilds_audio(r0_runtime, monkeypatch):
    p = image_project(voice=True)
    _, job = saved_job('render', p, {'preview': True})
    run = render.run_ff
    def fail_mux(args, **kwargs):
        if '-c' in args and 'copy' in args and str(args[-1]).endswith('.part.mp4'):
            raise ValueError('fixture: interrupted before mux')
        return run(args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(render, 'run_ff', fail_mux)
        with pytest.raises(ValueError, match='fixture:'):
            render.render(p, job, True)
    directory = store.project_dir(p.id) / 'exports' / job.id
    clip_hash = media.file_sha256(directory / 'clip_000.mp4')
    store.asset_path(p.id, store.get_asset(p, p.voice_id)).write_bytes(wave_bytes(880))
    monkeypatch.setattr(render, '_render_clip', lambda *args, **kwargs: pytest.fail('Unchanged clip must be reused'))
    result = render.render(p, job, True)
    assert result['clip_cache_hits'] == 1 and result['clip_cache_misses'] == 0
    assert result['clock_verified'] and clip_hash


def test_dependency_changed_during_mux_cannot_publish_a_result(r0_runtime, monkeypatch):
    p = image_project()
    _, job = saved_job('render', p, {'preview': True})
    run = render.run_ff
    def replace_after_mux(args, **kwargs):
        result = run(args, **kwargs)
        if '-c' in args and 'copy' in args and str(args[-1]).endswith('.part.mp4'):
            Image.new('RGB', (64, 64), 'blue').save(store.asset_path(p.id, p.assets[0]))
        return result
    monkeypatch.setattr(render, 'run_ff', replace_after_mux)
    with pytest.raises(InputBlocked, match='đã đổi trong lúc dựng'):
        render.render(p, job, True)
    assert store.read(p.id).exports == [] and not job._commit_done
