import pytest
from backend import jobs, media, store
from backend.models import Project


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    jobs.JOBS.clear()
    return tmp_path


@pytest.fixture
def client(isolated):
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_interrupted_jobs_are_persisted_and_not_restarted(isolated):
    project = store.save(Project(name='Cần phục hồi'))
    job = jobs.reserve(project.id, 'render')
    with job._state_lock:
        job.status = 'running'
        job.phase = 'processing'
        jobs._persist(job)

    jobs.initialize()

    saved = jobs.JOBS[job.id].dump()
    assert saved['status'] == 'interrupted'
    assert 'chủ động chạy lại' in saved['message']
    assert not jobs.busy(project.id)


def test_queued_job_can_be_cancelled_before_it_starts(isolated):
    project = store.save(Project())
    job = jobs.reserve(project.id, 'upload')

    result = jobs.cancel(job.id)

    assert result['status'] == 'cancelled'
    assert not jobs.busy(project.id)


def test_grouped_autosaves_undo_redo_and_new_branch(isolated):
    project = store.save(Project(name='Bản gốc'))
    group = 'a1b2c3d4e5f6a7b8'

    project.name = 'Bản nháp'
    project = store.save(project, operation_id=group)
    project.name = 'Bản nháp đã lưu'
    store.save(project, operation_id=group)

    history_files = [path for path in (store.project_dir(project.id) / 'history').glob('*.json')
                     if path.name != '.operation.json']
    assert len(history_files) == 1
    assert store.undo(project.id).name == 'Bản gốc'
    assert jobs.history_state(project.id)['can_redo']
    assert store.redo(project.id).name == 'Bản nháp đã lưu'

    current = store.read(project.id)
    current.name = 'Nhánh mới'
    store.save(current, operation_id='b2c3d4e5f6a7b8c9')
    assert not jobs.history_state(project.id)['can_redo']


def test_media_probe_runs_in_background_without_blocking_health(client, monkeypatch):
    import io
    import threading
    import time

    from PIL import Image

    project = client.post('/api/projects', json={}).json()
    payload = io.BytesIO()
    Image.new('RGB', (40, 60), 'green').save(payload, format='PNG')
    original_register = media.register
    started, release = threading.Event(), threading.Event()

    def slow_register(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return original_register(*args, **kwargs)

    monkeypatch.setattr(media, 'register', slow_register)
    response = client.post(
        f"/api/projects/{project['id']}/assets",
        files={'files': ('slow.png', payload.getvalue(), 'image/png')},
        data={'role': 'overlay'},
    )
    assert response.status_code == 200
    job_id = response.json()['job']['id']
    try:
        assert started.wait(2)
        assert client.get('/api/health').status_code == 200
        jobs_during_probe = client.get('/api/jobs?pid=' + project['id']).json()
        assert next(job for job in jobs_during_probe if job['id'] == job_id)['status'] == 'running'
    finally:
        release.set()
    for _ in range(100):
        result = next(job for job in client.get('/api/jobs?pid=' + project['id']).json()
                      if job['id'] == job_id)
        if result['status'] in ('done', 'error', 'cancelled'):
            break
        time.sleep(.02)
    assert result['status'] == 'done', result
