from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import jobs, media, planner, render, store
from backend.main import app
from backend.models import Asset, Clip, Cue, Layer, Project, Scene, Template
from backend.preflight import preflight
from tests.fixtures.factory import make_media_project, make_transparent_logo


@pytest.fixture
def m2_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    return tmp_path


@pytest.fixture
def m2_client(m2_root):
    return TestClient(app)


def test_schema_v1_migration_preserves_existing_cues_as_edited(m2_root):
    project = Project(script='Câu mới.', cues=[Cue(start=0, end=2, text='Cue cũ.')])
    data = project.model_dump()
    data['schema_version'] = 1
    data.pop('cues_edited')
    data.pop('cues_stale')
    store.atomic(store.project_dir(project.id) / 'project.json', data)

    loaded = store.read(project.id)
    assert loaded.schema_version == 5
    assert loaded.cues_edited is True
    assert loaded.cues_stale is False
    assert loaded.cues[0].text == 'Cue cũ.'


def test_script_change_keeps_cues_and_explicit_regenerate_replaces_them(m2_client):
    project = store.save(Project(
        script='Kịch bản cũ.', target_duration=6,
        cues=[Cue(start=1, end=3, text='Phụ đề đã sửa.')],
        cues_edited=True, cue_timing='estimated',
    ))
    edited = project.model_copy(deep=True)
    edited.script = 'Kịch bản hoàn toàn mới. Câu thứ hai.'
    response = m2_client.put(f'/api/projects/{project.id}', json=edited.model_dump())
    assert response.status_code == 200, response.text
    changed = Project.model_validate(response.json())
    assert changed.cues == project.cues
    assert changed.cues_stale is True
    assert changed.voice_id == ''

    regenerated = m2_client.post(f'/api/projects/{project.id}/cues/regenerate',
                                 json={'revision': changed.revision})
    assert regenerated.status_code == 200, regenerated.text
    current = Project.model_validate(regenerated.json())
    assert current.cues != project.cues
    assert [cue.text for cue in current.cues] == ['Kịch bản hoàn toàn mới.', 'Câu thứ hai.']
    assert current.cues_edited is False and current.cues_stale is False
    assert any('Chọn lại hoặc tạo lại voice' in warning for warning in current.warnings)


def test_preflight_api_and_renderer_report_the_same_missing_source_file(m2_client):
    source = Asset(
        name='Nguồn bị thiếu', filename='missing.mp4', role='source', media='video', duration=4,
        scenes=[Scene(start=0, end=4)],
    )
    project = store.save(Project(assets=[source], clips=[Clip(asset_id=source.id, duration=3)]))
    report = m2_client.get(f'/api/projects/{project.id}/preflight')
    assert report.status_code == 200
    issue = next(item for item in report.json()['issues'] if item['code'] == 'asset.file_missing')
    assert issue['severity'] == 'error'
    assert issue['entity'] == {'type': 'asset', 'id': source.id}
    api_render = m2_client.post(f'/api/projects/{project.id}/render', json={'preview': True})
    assert api_render.status_code == 400
    assert any(item['code'] == 'asset.file_missing'
               for item in api_render.json()['detail']['issues'])
    with pytest.raises(ValueError, match='Preflight asset.file_missing'):
        render.render(project, jobs.Job(project.id, 'missing-file'), preview=True)


def test_preflight_flags_cue_overrun_and_subtitle_writer_does_not_clip_it(m2_root):
    source = Asset(name='Nguồn', filename='source.mp4', role='source', media='video', duration=4,
                   scenes=[Scene(start=0, end=4)])
    project = Project(assets=[source], clips=[Clip(asset_id=source.id, duration=2)],
                      cues=[Cue(start=1, end=3, text='Cue cần giữ nguyên.')])
    report = preflight(project)
    issue = next(item for item in report['issues'] if item['code'] == 'cue.outside_timeline')
    assert issue['severity'] == 'error'

    directory = store.project_dir(project.id) / 'exports' / 'subtitles'
    directory.mkdir(parents=True)
    render.subtitles(project, 720, 1280, directory)
    srt = (directory / 'captions.srt').read_text(encoding='utf-8-sig')
    assert '00:00:01,000 --> 00:00:03,000' in srt
    assert 'Cue cần giữ nguyên.' in srt


def test_edited_cues_survive_replan_render_and_reopen(m2_root):
    project = make_media_project(include_voice=False, include_music=False)
    source = next(asset for asset in project.assets if asset.role == 'source')
    source.scenes = [Scene(start=0, end=5)]
    project.cues = [Cue(start=.4, end=2.2, text='Nội dung đã chỉnh tay.'),
                    Cue(start=2.5, end=4.8, text='Mốc thời gian được giữ.')]
    project.cues_edited = True
    expected = [cue.model_copy(deep=True) for cue in project.cues]
    planned = planner.plan(project, False, jobs.Job(project.id, 'edited-cue-plan'))
    assert planned.cues == expected
    store.save(planned)

    result = render.render(planned, jobs.Job(project.id, 'edited-cue-render'), preview=True)
    export_dir = store.project_dir(project.id) / 'exports' / result['id']
    snapshot = Project.model_validate_json((export_dir / 'project.json').read_text(encoding='utf-8'))
    reopened = store.read(project.id)
    assert snapshot.cues == expected
    assert reopened.cues == expected


def test_template_package_copies_and_remaps_all_layer_logo_references(m2_client, m2_root):
    source_project = Project(name='Dự án nguồn')
    source_dir = store.project_dir(source_project.id) / 'assets'
    logo_path = make_transparent_logo(source_dir / 'brand.png')
    logo = Asset(name='Logo thương hiệu', filename=logo_path.name, role='overlay',
                 **media.probe(logo_path))
    source_project.assets = [logo]
    source_project.template = Template(
        name='Mẫu nhận diện',
        layers=[Layer(kind='image', asset_id=logo.id)],
        slot_durations=[2],
        slot_layers=[[Layer(kind='image', asset_id=logo.id, x=.5)]],
    )
    store.save(source_project)

    saved = m2_client.post(f'/api/projects/{source_project.id}/templates',
                           json=source_project.template.model_dump())
    assert saved.status_code == 200, saved.text
    package = saved.json()
    package_dir = m2_root / 'templates' / package['id']
    manifest = json.loads((package_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert (package_dir / 'template.json').is_file()
    assert manifest['resources'][logo.id]['sha256']

    target = store.save(Project(name='Dự án đích'))
    applied = m2_client.post(f"/api/projects/{target.id}/templates/{package['id']}/apply", json={})
    assert applied.status_code == 200, applied.text
    result = Project.model_validate(applied.json())
    copied_id = result.template.layers[0].asset_id
    assert copied_id != logo.id
    assert result.template.slot_layers[0][0].asset_id == copied_id
    assert store.asset_path(result.id, store.get_asset(result, copied_id)).is_file()

    applied_again = m2_client.post(f"/api/projects/{target.id}/templates/{package['id']}/apply", json={})
    assert applied_again.status_code == 200, applied_again.text
    repeated = Project.model_validate(applied_again.json())
    assert repeated.template.layers[0].asset_id != copied_id


def test_legacy_template_requests_replacement_for_missing_logo(m2_client, m2_root):
    target = Project(name='Dự án thay logo')
    image_dir = store.project_dir(target.id) / 'assets'
    replacement_path = make_transparent_logo(image_dir / 'replacement.png')
    replacement = Asset(name='Logo mới', filename=replacement_path.name, role='overlay',
                        **media.probe(replacement_path))
    target.assets = [replacement]
    store.save(target)
    missing_id = '7777777777777777'
    legacy = Template(id='8888888888888888', name='Mẫu cũ',
                      layers=[Layer(kind='image', asset_id=missing_id)])
    store.atomic(m2_root / 'templates' / f'{legacy.id}.json', legacy.model_dump())

    package_list = m2_client.get('/api/template-packages').json()
    package = next(item for item in package_list if item['id'] == legacy.id)
    assert package['missing_assets'] == [{'id': missing_id, 'name': 'Logo / biểu tượng'}]
    refused = m2_client.post(f'/api/projects/{target.id}/templates/{legacy.id}/apply', json={})
    assert refused.status_code == 409
    assert refused.json()['detail']['missing_assets'][0]['id'] == missing_id

    applied = m2_client.post(f'/api/projects/{target.id}/templates/{legacy.id}/apply',
                             json={'asset_map': {missing_id: replacement.id}})
    assert applied.status_code == 200, applied.text
    result = Project.model_validate(applied.json())
    assert result.template.layers[0].asset_id == replacement.id


def test_legacy_template_uses_original_project_logo_metadata_when_available(m2_client, m2_root):
    source_project = Project(name='Dự án còn logo cũ')
    source_dir = store.project_dir(source_project.id) / 'assets'
    logo_path = make_transparent_logo(source_dir / 'legacy-brand.png')
    logo = Asset(name='Logo legacy', filename=logo_path.name, role='overlay',
                 **media.probe(logo_path))
    source_project.assets = [logo]
    store.save(source_project)
    legacy = Template(id='9999999999999999', name='Template JSON cũ',
                      layers=[Layer(kind='image', asset_id=logo.id)])
    store.atomic(m2_root / 'templates' / f'{legacy.id}.json', legacy.model_dump())

    listed = m2_client.get('/api/template-packages').json()
    package = next(item for item in listed if item['id'] == legacy.id)
    assert package['missing_assets'] == []
    target = store.save(Project(name='Dự án đích'))
    applied = m2_client.post(f'/api/projects/{target.id}/templates/{legacy.id}/apply', json={})
    assert applied.status_code == 200, applied.text
    result = Project.model_validate(applied.json())
    copied_id = result.template.layers[0].asset_id
    assert copied_id != logo.id
    assert store.asset_path(result.id, store.get_asset(result, copied_id)).is_file()
