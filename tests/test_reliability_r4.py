"""R4 durable upload follow-up idempotency and discard/storage contract."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from backend import jobs, job_store, main, store
from backend.models import Project

pytestmark = pytest.mark.reliability_r4


def completed_upload(project):
    job = jobs.reserve(project.id, 'upload', payload={}, snapshot=project.model_dump())
    job.status = job.phase = 'done'
    job.result = {'project_id': project.id, 'revision': project.revision, 'auto_assemble': True}
    jobs._persist(job)
    return job


def test_two_tabs_auto_assemble_reserve_one_durable_job(r0_runtime):
    p = store.save(Project())
    upload = completed_upload(p)
    client = TestClient(main.app)
    def post(_):
        response = client.post(f'/api/projects/{p.id}/assemble-audio', json={'upload_job_id': upload.id})
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(post, range(16)))
    assert len({item['id'] for item in results}) == 1
    followup = jobs.JOBS[results[0]['id']]
    assert followup.payload == {'upload_job_id': upload.id}
    assert followup.attempt == 1 and followup.dispatch_ready
    assert len([job for job in jobs.JOBS.values() if job.kind == 'assemble-audio']) == 1


@pytest.mark.parametrize('state', ['queued', 'error', 'done', 'cancelled'])
def test_lost_response_and_restart_reuse_followup_even_after_terminal_state(r0_runtime, state):
    p = store.save(Project())
    upload = completed_upload(p)
    first = main.assemble_audio(p.id, {'upload_job_id': upload.id})
    followup = jobs.JOBS[first['id']]
    followup.status = followup.phase = state
    followup.dispatch_ready = state == 'queued'
    jobs._persist(followup)
    jobs.initialize()
    second = main.assemble_audio(p.id, {'upload_job_id': upload.id})
    assert second['id'] == first['id']
    assert len([job for job in jobs.JOBS.values() if job.kind == 'assemble-audio']) == 1
    assert second['attempt'] == 1
    if state == 'queued':
        assert second['status'] == 'interrupted' and jobs.JOBS[second['id']]._future is None


def test_reserved_followup_before_launch_is_recoverable_without_duplicate(r0_runtime, monkeypatch):
    p = store.save(Project())
    upload = completed_upload(p)
    original = jobs.launch
    monkeypatch.setattr(jobs, 'launch', lambda *a, **kw: (_ for _ in ()).throw(OSError('lost after reserve')))
    with pytest.raises(OSError):
        main.assemble_audio(p.id, {'upload_job_id': upload.id})
    reserved = next(job for job in jobs.JOBS.values() if job.kind == 'assemble-audio')
    monkeypatch.setattr(jobs, 'launch', original)
    replay = main.assemble_audio(p.id, {'upload_job_id': upload.id})
    assert replay['id'] == reserved.id and replay['dispatch_ready']


def test_pre_r4_followup_is_linked_without_changing_its_fingerprint(r0_runtime):
    p = store.save(Project())
    upload = completed_upload(p)
    legacy = jobs.reserve(p.id, 'assemble-audio', payload={}, snapshot=p.model_dump(), config_fingerprint=main._job_config_fingerprint('assemble-audio'))
    original_fingerprint = legacy.input_fingerprint
    result = main.assemble_audio(p.id, {'upload_job_id': upload.id})
    assert result['id'] == legacy.id and legacy.parent_job_id == upload.id
    assert legacy.payload == {} and legacy.input_fingerprint == original_fingerprint
    jobs.initialize()
    assert main.assemble_audio(p.id, {'upload_job_id': upload.id})['id'] == legacy.id


@pytest.mark.parametrize('fault', ['missing', 'wrong_project', 'wrong_kind', 'not_done', 'not_requested', 'invalid_id'])
def test_upload_followup_rejects_invalid_origin_without_creating_work(r0_runtime, fault):
    p = store.save(Project())
    upload = completed_upload(p)
    data = {'upload_job_id': upload.id}
    if fault == 'missing': data['upload_job_id'] = '0' * 16
    if fault == 'wrong_project': upload.pid = store.save(Project()).id
    if fault == 'wrong_kind': upload.kind = 'render'
    if fault == 'not_done': upload.status = 'error'
    if fault == 'not_requested': upload.result['auto_assemble'] = False
    if fault == 'invalid_id': data['upload_job_id'] = []
    response = TestClient(main.app).post(f'/api/projects/{p.id}/assemble-audio', json=data)
    assert response.status_code == 409
    assert not any(job.kind == 'assemble-audio' for job in jobs.JOBS.values())


@pytest.mark.parametrize('status', ['error', 'interrupted', 'blocked'])
def test_discard_unpins_cache_refreshes_storage_and_keeps_sources_and_published_export(r0_runtime, status):
    p = store.save(Project())
    original = store.project_dir(p.id) / 'assets' / 'source.bin'
    published = store.project_dir(p.id) / 'exports' / 'published' / 'video.mp4'
    cache = store.ROOT / 'cache' / 'shared.bin'
    for path in [original, published, cache]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'keep me')
    p.exports = [{'id': 'published', 'filename': 'video.mp4'}]
    p = store.save(p)
    job = jobs.reserve(p.id, 'render', snapshot=p.model_dump())
    incomplete = store.project_dir(p.id) / 'exports' / job.id / 'video.mp4'
    incomplete.parent.mkdir(parents=True)
    incomplete.write_bytes(b'not committed')
    job.save_checkpoint('fixture', 'hash', {'ready': True})
    job.status = job.phase = status
    jobs._persist(job)
    client = TestClient(main.app)
    assert jobs.has_pinned_artifacts(p.id)
    assert client.post('/api/storage/cleanup-cache', json={}).status_code == 400
    before = client.get('/api/storage').json()
    response = client.post(f'/api/jobs/{job.id}/cancel', json={})
    assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    after = client.get('/api/storage').json()
    assert not jobs.has_pinned_artifacts(p.id) and not incomplete.exists()
    assert after['categories']['exports'] < before['categories']['exports']
    assert client.post('/api/storage/cleanup-cache', json={}).status_code == 200
    assert not cache.exists() and original.read_bytes() == published.read_bytes() == b'keep me'
    assert store.read(p.id).exports == p.exports


def test_discard_after_commit_fault_preserves_export_and_recovers_job(r0_runtime, monkeypatch):
    p = store.save(Project())
    job = jobs.reserve(p.id, 'render', snapshot=p.model_dump())
    exported = store.project_dir(p.id) / 'exports' / job.id / 'video.mp4'
    exported.parent.mkdir(parents=True); exported.write_bytes(b'committed')
    write_receipt = job_store.write_receipt
    monkeypatch.setattr(job_store, 'write_receipt', lambda *a, **kw: (_ for _ in ()).throw(OSError('receipt fault')))
    def commit():
        current = store.read(p.id); current.exports.append({'id': job.id, 'filename': 'video.mp4'}); return store.save(current)
    with pytest.raises(OSError): job.commit(commit, project_id=p.id, allow_latest=True)
    job.status = job.phase = 'error'
    monkeypatch.setattr(job_store, 'write_receipt', write_receipt)
    response = TestClient(main.app).post(f'/api/jobs/{job.id}/cancel', json={})
    assert response.status_code == 200 and response.json()['status'] == 'done'
    assert exported.read_bytes() == b'committed'
    assert store.read(p.id).exports[0]['id'] == job.id
