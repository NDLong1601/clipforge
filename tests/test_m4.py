import json
import math
import struct
import wave

import pytest

from backend import jobs, planner, store
from backend.main import app
from backend.models import Asset, Clip, Project, Scene
from backend.preflight import preflight


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    jobs.JOBS.clear()
    return tmp_path


def test_schema_v3_migration_adds_unlocked_clip_default(isolated):
    source = Asset(name='Nguồn', filename='source.mp4', role='source', media='video', duration=8,
                   scenes=[Scene(start=0, end=8)])
    project = Project(assets=[source], clips=[Clip(asset_id=source.id, duration=3, locked=True)])
    data = project.model_dump()
    data['schema_version'] = 3
    data['clips'][0].pop('locked')
    path = store.project_dir(project.id) / 'project.json'
    store.atomic(path, data)

    migrated = store.read(project.id)

    assert migrated.schema_version == 5
    assert migrated.clips[0].locked is False


def test_planner_keeps_locked_clip_position_and_duration(isolated):
    source = Asset(name='Nguồn', filename='source.mp4', role='source', media='video', duration=20,
                   scenes=[Scene(start=0, end=20, tags='biển')])
    before = Clip(asset_id=source.id, scene_id=source.scenes[0].id, source_start=1,
                  duration=2, title='vùng cần thay')
    anchor = Clip(asset_id=source.id, scene_id=source.scenes[0].id, source_start=7,
                  duration=3, speed=1.5, title='đã duyệt', locked=True)
    after = Clip(asset_id=source.id, scene_id=source.scenes[0].id, source_start=12,
                 duration=2, title='vùng cần thay')
    project = Project(target_duration=10, assets=[source], clips=[before, anchor, after])

    planned = planner.plan(project, False, jobs.Job(project.id, 'plan-locked'))

    anchor_index = next(i for i, clip in enumerate(planned.clips) if clip.id == anchor.id)
    assert math.isclose(sum(c.duration for c in planned.clips[:anchor_index]), 2, abs_tol=1e-6)
    assert planned.clips[anchor_index].model_dump() == anchor.model_dump()
    assert math.isclose(sum(c.duration for c in planned.clips), 10, abs_tol=1e-6)
    assert all(clip.locked for clip in planned.clips if clip.id == anchor.id)


def test_planner_reports_locked_clip_beyond_target(isolated):
    source = Asset(name='Nguồn', filename='source.mp4', role='source', media='video', duration=20,
                   scenes=[Scene(start=0, end=20)])
    clips = [Clip(asset_id=source.id, duration=4),
             Clip(asset_id=source.id, duration=3, locked=True)]
    project = Project(target_duration=6, assets=[source], clips=clips)

    with pytest.raises(ValueError, match='vượt thời lượng đích'):
        planner.plan(project, False, jobs.Job(project.id, 'plan-conflict'))


def test_preflight_reports_frame_hold_using_speed_and_scene_end():
    source = Asset(name='Nguồn', filename='source.mp4', role='source', media='video', duration=6,
                   fps=30, scenes=[Scene(start=2, end=4)])
    clip = Clip(asset_id=source.id, scene_id=source.scenes[0].id, source_start=3.8,
                duration=2, speed=.5)
    report = preflight(Project(assets=[source], clips=[clip]))
    issue = next(item for item in report['issues'] if item['code'] == 'clip.source_frame_hold')

    assert issue['severity'] == 'warning'
    assert '1.60s' in issue['message']
    assert '×0.5' in issue['message']


def test_waveform_endpoint_returns_and_reuses_cached_audio_peaks(isolated):
    from fastapi.testclient import TestClient

    project = Project()
    directory = store.project_dir(project.id) / 'assets'
    directory.mkdir(parents=True)
    path = directory / 'voice.wav'
    rate = 8000
    samples = [int(14000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate // 2)]
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
    voice = Asset(name='Voice', filename=path.name, role='voice', media='audio', duration=.5,
                  has_audio=True)
    project.assets = [voice]
    store.save(project)

    with TestClient(app) as client:
        response = client.get(f'/api/projects/{project.id}/assets/{voice.id}/waveform?max_points=200')
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['asset_id'] == voice.id
        assert payload['duration'] == .5
        assert len(payload['peaks']) <= 200
        assert max(payload['peaks']) > .3

        cache_files = list((isolated / 'cache' / 'waveforms').glob('*.json'))
        assert len(cache_files) == 1
        cached = json.loads(cache_files[0].read_text(encoding='utf-8'))
        assert cached['peak_rate'] == 100

        second = client.get(f'/api/projects/{project.id}/assets/{voice.id}/waveform?max_points=64')
        assert second.status_code == 200
        assert len(list((isolated / 'cache' / 'waveforms').glob('*.json'))) == 1
