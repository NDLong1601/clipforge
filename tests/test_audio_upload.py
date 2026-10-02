import io
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import jobs, store
from backend.main import app
from backend.models import Project


@pytest.fixture
def audio_client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    return TestClient(app)


def finish_upload(client, pid, files, role):
    response = client.post(f'/api/projects/{pid}/assets', files=files, data={'role': role})
    assert response.status_code == 200, response.text
    job = jobs.JOBS[response.json()['job']['id']]
    job._future.result(timeout=15)
    assert job.status == 'done', job.dump()
    assert not (store.ROOT / 'staging' / pid / job.id).exists()
    return client.get(f'/api/projects/{pid}').json()


@pytest.mark.parametrize('role', ['voice', 'music'])
@pytest.mark.parametrize('with_source', [False, True])
def test_audio_upload_without_thumbnail_preserves_project_files(audio_client, role, with_source):
    project = store.save(Project(script='Giọng đọc thử. Câu tiếp theo.'))
    thumbs = store.project_dir(project.id) / 'thumbs'
    existing_thumbs = {}
    if with_source:
        image = io.BytesIO()
        Image.new('RGB', (40, 60), 'green').save(image, format='PNG')
        finish_upload(audio_client, project.id,
                      [('files', ('source.png', image.getvalue(), 'image/png'))], 'source')
        existing_thumbs = {path.name: path.read_bytes() for path in thumbs.iterdir()}
        assert existing_thumbs
    before = store.read(project.id)

    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b'\0\0' * 16000)
    content = audio.getvalue()
    current = finish_upload(audio_client, project.id,
                            [('files', ('first.wav', content, 'audio/wav')),
                             ('files', ('second.wav', content, 'audio/wav'))], role)

    assert current['revision'] == before.revision + 1
    assert current['assets'][:-2] == [asset.model_dump() for asset in before.assets]
    assert current[f'{role}_id'] == current['assets'][-1]['id']
    for asset in current['assets'][-2:]:
        assert asset['media'] == 'audio' and asset['thumbnail'] == ''
        assert asset['duration'] == pytest.approx(2)
        response = audio_client.get(f"/api/projects/{project.id}/assets/{asset['id']}/file")
        assert response.status_code == 200 and response.content == content
    assert {path.name: path.read_bytes() for path in thumbs.iterdir()} == existing_thumbs
    if role == 'voice':
        assert current['cue_timing'] == 'estimated'
        assert [cue['text'] for cue in current['cues']] == ['Giọng đọc thử.', 'Câu tiếp theo.']
        assert current['cues'][-1]['end'] == pytest.approx(2)
