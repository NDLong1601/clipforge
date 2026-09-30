from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import jobs, store
from backend.main import app
from backend.models import Asset, Clip, Layer, Project, Scene, Template
from backend.template_utils import compressed_slot_durations


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    return tmp_path


@pytest.fixture
def client(isolated_store):
    return TestClient(app)


def test_project_save_checks_references_before_snapshot_or_replace(isolated_store):
    project = store.save(Project(name='Bản an toàn'))
    project.voice_id = 'missing-asset'

    with pytest.raises(ValueError, match=r'voice_id.*không tìm thấy'):
        store.save(project)

    assert store.read(project.id).name == 'Bản an toàn'
    assert list((store.project_dir(project.id) / 'history').glob('*.json')) == []


def test_project_save_rejects_missing_image_layer_asset(isolated_store):
    project = store.save(Project(name='Template hợp lệ'))
    project.template.layers = [Layer(kind='image', asset_id='missing-logo')]

    with pytest.raises(ValueError, match=r'template\.layers.*asset_id.*không tìm thấy'):
        store.save(project)

    assert store.read(project.id).template.layers == []


def test_physical_file_validation_is_separate_from_structure(isolated_store):
    asset = Asset(name='Video nguồn', filename='missing.mp4', role='source', media='video', duration=4)
    project = store.save(Project(assets=[asset]))
    assert store.read(project.id).assets[0].id == asset.id

    with pytest.raises(FileNotFoundError, match='missing.mp4'):
        store.validate_files(project)


def test_atomic_failure_keeps_current_file_and_recovery_snapshot(isolated_store, monkeypatch):
    project = store.save(Project(name='Trước khi ghi lỗi'))
    original_atomic = store.atomic

    def fail_current(path, data):
        if path.name == 'project.json':
            raise OSError('disk full')
        return original_atomic(path, data)

    monkeypatch.setattr(store, 'atomic', fail_current)
    project.name = 'Không được ghi'
    with pytest.raises(OSError, match='disk full'):
        store.save(project)

    assert store.read(project.id).name == 'Trước khi ghi lỗi'
    snapshots = list((store.project_dir(project.id) / 'history').glob('*.json'))
    assert len(snapshots) == 1
    assert store._read_project_file(snapshots[0]).name == 'Trước khi ghi lỗi'
    assert not list((store.project_dir(project.id)).glob('*.tmp'))


def test_old_project_data_migrates_to_current_schema(isolated_store):
    project = Project(name='Dữ liệu cũ')
    path = store.project_dir(project.id) / 'project.json'
    data = project.model_dump()
    data.pop('schema_version')
    store.atomic(path, data)

    loaded = store.read(project.id)
    assert loaded.schema_version == 3
    assert store.save(loaded).schema_version == 3
    assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 3


def test_broken_project_isolated_and_recovered_from_history(client, isolated_store):
    valid = store.save(Project(name='Dự án còn tốt'))
    broken = store.save(Project(name='Có lịch sử'))
    broken.name = 'Phiên bản mới hơn'
    store.save(broken)
    broken_path = store.project_dir(broken.id) / 'project.json'
    broken_bytes = b'{ this is deliberately broken'
    broken_path.write_bytes(broken_bytes)

    rows = client.get('/api/projects').json()
    by_id = {item['id']: item for item in rows}
    assert by_id[valid.id]['name'] == 'Dự án còn tốt'
    assert by_id[broken.id]['corrupt'] is True
    assert by_id[broken.id]['recovery_available'] is True

    response = client.post(f'/api/projects/{broken.id}/recover', json={})
    assert response.status_code == 200, response.text
    assert response.json()['name'] == 'Có lịch sử'
    preserved = list(store.project_dir(broken.id).glob('project.json.corrupt-*'))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == broken_bytes
    assert client.get('/api/projects').json()[0]['corrupt'] is False


def test_soft_delete_then_undo_restores_edit_state_and_keeps_later_assets(client, isolated_store):
    source = Asset(id='1000000000000001', name='Nguồn', filename='source.mp4', role='source',
                   media='video', duration=8, scenes=[Scene(id='2000000000000001', start=0, end=4)])
    voice = Asset(id='1000000000000002', name='Voice', filename='voice.wav', role='voice',
                  media='audio', duration=8, has_audio=True)
    music = Asset(id='1000000000000003', name='Nhạc', filename='music.wav', role='music',
                  media='audio', duration=8, has_audio=True)
    logo = Asset(id='1000000000000004', name='Logo', filename='logo.png', role='overlay', media='image')
    project = Project(name='Gỡ tư liệu', assets=[source, voice, music, logo],
                      clips=[Clip(asset_id=source.id, scene_id=source.scenes[0].id,
                                  source_start=0, duration=2,
                                  layers=[Layer(kind='image', asset_id=logo.id)])],
                      template=Template(layers=[Layer(kind='image', asset_id=logo.id)]),
                      voice_id=voice.id, music_id=music.id)
    asset_dir = store.project_dir(project.id) / 'assets'
    asset_dir.mkdir(parents=True)
    for asset in project.assets:
        (asset_dir / asset.filename).write_bytes(asset.name.encode('utf-8'))
    store.save(project)

    for asset in (source, voice, music, logo):
        response = client.delete(f'/api/projects/{project.id}/assets/{asset.id}')
        assert response.status_code == 200, response.text
        project = Project.model_validate(response.json())
    assert project.clips == []
    assert project.voice_id == project.music_id == ''
    assert all(asset.deleted for asset in project.assets)
    assert project.template.layers[0].asset_id == ''
    assert all((asset_dir / asset.filename).read_bytes() == asset.name.encode('utf-8')
               for asset in project.assets if asset.id != '1000000000000005')

    later = Asset(id='1000000000000005', name='Nhập sau', filename='later.mp4', role='source', media='video')
    project.assets.append(later)
    store.save(project)
    for _ in range(5):
        restored = store.undo(project.id)

    assert restored.voice_id == voice.id
    assert restored.music_id == music.id
    assert len(restored.clips) == 1 and restored.clips[0].asset_id == source.id
    assert restored.template.layers[0].asset_id == logo.id
    assert not any(asset.deleted for asset in restored.assets if asset.id in {source.id, voice.id, music.id, logo.id})
    assert any(asset.id == later.id for asset in restored.assets)


def test_template_compression_preserves_scenes_order_and_full_tail():
    scenes = [Scene(id=f'{index + 1:016x}', start=index, end=index + 1) for index in range(101)]
    asset = Asset(name='101 cảnh', filename='reference.mp4', role='reference', media='video',
                  duration=104, scenes=scenes)
    durations = compressed_slot_durations(asset)
    assert len(durations) == 100
    assert sum(durations) == pytest.approx(104)
    assert len(asset.scenes) == 101
    assert sum(durations) >= asset.scenes[-1].end  # Includes the three seconds of tail silence.


def test_environment_settings_are_not_persisted_and_saved_keys_are_preserved(isolated_store, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-ai-secret')
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'environment-azure-secret')
    store.save_settings({'tts_provider': 'windows'})
    public = store.public_settings()
    store.save_settings(public)
    persisted = (isolated_store / 'settings.json').read_text(encoding='utf-8')
    assert 'environment-ai-secret' not in persisted
    assert 'environment-azure-secret' not in persisted

    store.save_settings({'ai_key': 'saved-ai-secret', 'azure_key': 'saved-azure-secret'})
    before = json.loads((isolated_store / 'settings.json').read_text(encoding='utf-8'))
    public = store.public_settings()
    public['tts_provider'] = 'windows'
    store.save_settings(public)
    after = json.loads((isolated_store / 'settings.json').read_text(encoding='utf-8'))
    assert before['ai_key'] == 'saved-ai-secret'
    assert any(item['api_key'] == 'saved-ai-secret' for item in after['ai_profiles'])
    assert before['azure_key'] == after['azure_key'] == 'saved-azure-secret'


def test_using_environment_key_requires_explicit_action(isolated_store, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-ai-secret')
    store.save_settings({'ai_key': 'saved-ai-secret'})
    before = json.loads((isolated_store / 'settings.json').read_text(encoding='utf-8'))
    assert before['ai_key'] == 'saved-ai-secret'

    result = store.use_environment_secret('ai')
    after = json.loads((isolated_store / 'settings.json').read_text(encoding='utf-8'))
    assert after['ai_key'] == ''
    assert after['ai_profiles'][0]['api_key'] == ''
    assert result['secret_sources']['ai_profiles']['legacy'] == 'environment'
    assert 'environment-ai-secret' not in json.dumps(after)


def test_data_directory_lock_blocks_another_process(isolated_store):
    env = dict(os.environ, CLIPFORGE_DATA=str(isolated_store))
    root = Path(__file__).resolve().parents[1]
    python = sys.executable
    holder_code = "from backend import store; store.acquire_instance_lock(); print('locked', flush=True); input()"
    second_code = "from backend import store\ntry: store.acquire_instance_lock()\nexcept RuntimeError: print('blocked'); raise SystemExit(0)\nraise SystemExit(2)"
    holder = subprocess.Popen([python, '-c', holder_code], cwd=root, env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
    try:
        assert holder.stdout.readline().strip() == 'locked'
        second = subprocess.run([python, '-c', second_code], cwd=root, env=env,
                                capture_output=True, text=True, timeout=10)
        assert second.returncode == 0
        assert 'blocked' in second.stdout
    finally:
        holder.terminate()
        holder.wait(timeout=10)
