from __future__ import annotations

import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend import jobs, media, render, store
from backend.main import app
from backend.models import Asset, Clip, Cue, Layer, Project, Scene, Template
from tests.fixtures.factory import make_media_project, make_transparent_logo


@pytest.fixture
def review_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    return tmp_path


@pytest.fixture
def review_client(review_root):
    return TestClient(app)


def _write_legacy(project: Project, path, version: int) -> bytes:
    data = project.model_dump()
    if version == 0:
        data.pop('schema_version', None)
        data.pop('cues_edited', None)
        data.pop('cues_stale', None)
    else:
        data['schema_version'] = version
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _legacy_media_project(root, *, project_id=None):
    project = Project(id=project_id or Project().id, name='Tương thích dữ liệu cũ')
    asset_dir = store.project_dir(project.id) / 'assets'
    asset_dir.mkdir(parents=True, exist_ok=True)
    source_path = asset_dir / 'source.mp4'
    media.run_ff([
        '-f', 'lavfi', '-i', 'testsrc2=size=240x320:rate=30', '-t', '4', '-an',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', source_path,
    ])
    music_path = asset_dir / 'music-with-video.mp4'
    media.run_ff([
        '-f', 'lavfi', '-i', 'testsrc2=size=240x320:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=48000', '-t', '4',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', music_path,
    ])
    logo_path = make_transparent_logo(asset_dir / 'old-logo.png')
    source = Asset(name='Video nguồn', filename=source_path.name, role='source',
                   **media.probe(source_path))
    music = Asset(name='Nhạc dạng video', filename=music_path.name, role='music',
                  **media.probe(music_path))
    logo = Asset(name='Logo cũ', filename=logo_path.name, role='overlay',
                 **media.probe(logo_path))
    project.assets = [source, music, logo]
    project.clips = [Clip(asset_id=source.id, duration=4)]
    project.music_id = music.id
    project.template = Template(
        layers=[Layer(kind='text', asset_id=logo.id, text='Tiêu đề')],
        slot_layers=[[Layer(kind='rect', asset_id=logo.id)],
                     [Layer(kind='circle', asset_id=logo.id)]],
    )
    return project


def test_legacy_video_music_and_stale_shape_layer_migrate_save_and_render(review_root):
    project = _legacy_media_project(review_root)
    raw_path = store.project_dir(project.id) / 'project.json'
    _write_legacy(project, raw_path, 2)

    loaded = store.read(project.id)
    assert loaded.schema_version == 5
    assert store.get_asset(loaded, loaded.music_id).media == 'video'
    assert store.get_asset(loaded, loaded.music_id).has_audio is True
    assert loaded.template.layers[0].asset_id == ''
    assert [layer.asset_id for group in loaded.template.slot_layers for layer in group] == ['', '']

    saved = store.save(loaded)
    rendered = render.render(saved, jobs.Job(saved.id, 'legacy-video-music'), preview=True)
    output = store.project_dir(saved.id) / 'exports' / rendered['id'] / 'preview.mp4'
    assert output.is_file()
    assert media.probe(output)['has_audio'] is True


def test_recovery_migrates_legacy_history_with_video_music_and_stale_layer(review_root):
    project = _legacy_media_project(review_root)
    history = store.project_dir(project.id) / 'history' / '00000007.json'
    _write_legacy(project, history, 2)
    current = store.project_dir(project.id) / 'project.json'
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b'{ broken current file')

    restored = store.recover(project.id)
    assert restored.schema_version == 5
    assert restored.music_id == project.music_id
    assert store.get_asset(restored, restored.music_id).media == 'video'
    assert restored.template.layers[0].asset_id == ''
    assert store.read(project.id).template.slot_layers[0][0].asset_id == ''


def test_current_schema_still_rejects_asset_reference_on_text_layer(review_root):
    image = Asset(name='Logo', filename='logo.png', role='overlay', media='image')
    project = store.save(Project(assets=[image]))
    project.template.layers = [Layer(kind='text', asset_id=image.id)]
    with pytest.raises(ValueError, match='chỉ lớp ảnh mới được tham chiếu tư liệu'):
        store.save(project)


def test_schema_backup_preserves_exact_original_media_and_restore_instructions(review_root):
    project = Project(name='Backup nguyên bản',
                      assets=[Asset(name='Video nguồn', filename='source.mp4', role='source',
                                    media='video', duration=3)])
    asset_path = store.asset_path(project.id, project.assets[0])
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b'legacy media bytes')
    project_path = store.project_dir(project.id) / 'project.json'
    raw = _write_legacy(project, project_path, 0)

    migrated = store.read(project.id)
    store.save(migrated)
    backup_root = next((store.project_dir(project.id) / 'backups').glob('schema-v0-to-v5-*'))
    assert (backup_root / 'project.json').read_bytes() == raw
    assert (backup_root / 'assets' / 'source.mp4').read_bytes() == b'legacy media bytes'
    manifest = json.loads((backup_root / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['source_schema_version'] == 0
    assert 'Giải nén' in manifest['restore_steps']

    archive = store.create_schema_backup_archive(project.id, backup_root.name)
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read('project.json') == raw
        assert zipped.read('assets/source.mp4') == b'legacy media bytes'
        assert 'Giải nén' in json.loads(zipped.read('manifest.json'))['restore_steps']
    archive.unlink()


def test_failed_schema_backup_does_not_replace_project_or_create_history(review_root, monkeypatch):
    project = Project(name='Không được thay khi backup hỏng')
    path = store.project_dir(project.id) / 'project.json'
    raw = _write_legacy(project, path, 2)
    loaded = store.read(project.id)

    def fail_backup(*_args, **_kwargs):
        raise OSError('backup disk failure')

    monkeypatch.setattr(store, '_create_schema_backup', fail_backup)
    with pytest.raises(OSError, match='backup disk failure'):
        store.save(loaded)

    assert path.read_bytes() == raw
    assert list((path.parent / 'history').glob('*.json')) == []


def test_interrupted_upgrade_keeps_original_project_and_completed_backup(review_root, monkeypatch):
    project = Project(name='Dừng giữa lúc nâng schema')
    path = store.project_dir(project.id) / 'project.json'
    raw = _write_legacy(project, path, 0)
    migrated = store.read(project.id)
    original_atomic = store.atomic

    def fail_current(path_arg, data):
        if path_arg.name == 'project.json':
            raise OSError('interrupted before project replace')
        return original_atomic(path_arg, data)

    monkeypatch.setattr(store, 'atomic', fail_current)
    with pytest.raises(OSError, match='interrupted before project replace'):
        store.save(migrated)

    assert path.read_bytes() == raw
    backup_root = next((path.parent / 'backups').glob('schema-v0-to-v5-*'))
    assert (backup_root / 'project.json').read_bytes() == raw
    assert store.schema_backup_info(project.id)['available'] is True


def test_schema_backup_survives_history_pruning_after_many_saves(review_root):
    project = Project(name='Giữ bản gốc')
    path = store.project_dir(project.id) / 'project.json'
    raw = _write_legacy(project, path, 0)
    project = store.read(project.id)
    project = store.save(project)
    for index in range(30):
        project.name = f'Chỉnh sửa {index}'
        project = store.save(project)

    assert len(list((path.parent / 'history').glob('*.json'))) <= 25
    backup_root = next((path.parent / 'backups').glob('schema-v0-to-v5-*'))
    assert (backup_root / 'project.json').read_bytes() == raw
    assert store.schema_backup_info(project.id)['available'] is True


def test_schema_backup_keeps_distinct_originals_of_the_same_legacy_version(review_root):
    project = Project(name='Bản đầu')
    path = store.project_dir(project.id) / 'project.json'
    first_raw = _write_legacy(project, path, 0)
    store.save(store.read(project.id))

    project.name = 'Bản cũ được chỉnh sau đó'
    second_raw = _write_legacy(project, path, 0)
    store.save(store.read(project.id))

    backups = list((path.parent / 'backups').glob('schema-v0-to-v5-*'))
    assert len(backups) == 2
    preserved = {(backup / 'project.json').read_bytes() for backup in backups}
    assert preserved == {first_raw, second_raw}


def test_template_package_accepts_source_image_remaps_all_layers_and_renders(review_client, review_root):
    source = Project(name='Template dùng ảnh source')
    image_path = make_transparent_logo(store.project_dir(source.id) / 'assets' / 'brand.png')
    image = Asset(name='Logo nhập vai source', filename=image_path.name, role='source',
                  **media.probe(image_path))
    source.assets = [image]
    source.template = Template(
        name='Logo source', layers=[Layer(kind='image', asset_id=image.id)],
        slot_durations=[5], slot_layers=[[Layer(kind='image', asset_id=image.id, x=.5)]],
    )
    store.save(source)

    package_response = review_client.post(
        f'/api/projects/{source.id}/templates', json=source.template.model_dump())
    assert package_response.status_code == 200, package_response.text
    package_id = package_response.json()['id']

    target = make_media_project(include_voice=False, include_music=False)
    applied = review_client.post(f'/api/projects/{target.id}/templates/{package_id}/apply', json={})
    assert applied.status_code == 200, applied.text
    result = Project.model_validate(applied.json())
    copied_id = result.template.layers[0].asset_id
    assert copied_id != image.id
    assert result.template.slot_layers[0][0].asset_id == copied_id
    assert store.get_asset(result, copied_id).role == 'overlay'

    rendered = render.render(result, jobs.Job(result.id, 'source-image-template'), preview=True)
    output = store.project_dir(result.id) / 'exports' / rendered['id'] / 'preview.mp4'
    assert output.is_file()


def test_legacy_template_finds_source_role_image_for_remapping(review_client, review_root):
    source = Project(name='Legacy có ảnh source')
    image_path = make_transparent_logo(store.project_dir(source.id) / 'assets' / 'legacy.png')
    image = Asset(name='Ảnh source cũ', filename=image_path.name, role='source',
                  **media.probe(image_path))
    source.assets = [image]
    store.save(source)
    legacy = Template(id='9999999999999999', name='Mẫu legacy source',
                      layers=[Layer(kind='image', asset_id=image.id)],
                      slot_layers=[[Layer(kind='image', asset_id=image.id)]])
    store.atomic(review_root / 'templates' / f'{legacy.id}.json', legacy.model_dump())

    listed = review_client.get('/api/template-packages').json()
    package = next(item for item in listed if item['id'] == legacy.id)
    assert package['missing_assets'] == []
    target = store.save(Project(name='Đích legacy'))
    response = review_client.post(f'/api/projects/{target.id}/templates/{legacy.id}/apply', json={})
    assert response.status_code == 200, response.text
    result = Project.model_validate(response.json())
    copied_id = result.template.layers[0].asset_id
    assert result.template.slot_layers[0][0].asset_id == copied_id
    assert store.get_asset(result, copied_id).role == 'overlay'


def test_manual_cue_edit_clears_stale_and_preserves_other_cues(review_client, review_root):
    project = store.save(Project(
        script='Kịch bản mới.', target_duration=8,
        cues=[Cue(start=0, end=3, text='Cue thứ nhất cũ.'),
              Cue(start=3, end=6, text='Cue thứ hai cần giữ.')],
        cues_edited=True, cues_stale=False,
    ))
    changed_script = project.model_copy(deep=True)
    changed_script.script = 'Kịch bản đã thay đổi.'
    response = review_client.put(f'/api/projects/{project.id}', json=changed_script.model_dump())
    assert response.status_code == 200, response.text
    stale = Project.model_validate(response.json())
    assert stale.cues_stale is True

    manual = stale.model_copy(deep=True)
    manual.cues[0].text = 'Cue thứ nhất đã xem lại.'
    manual.cues_edited = True
    manual.cues_stale = False
    response = review_client.put(f'/api/projects/{project.id}', json=manual.model_dump())
    assert response.status_code == 200, response.text
    saved = Project.model_validate(response.json())
    reopened = Project.model_validate(review_client.get(f'/api/projects/{project.id}').json())
    assert saved.cues_stale is False and reopened.cues_stale is False
    assert [cue.text for cue in reopened.cues] == [
        'Cue thứ nhất đã xem lại.', 'Cue thứ hai cần giữ.',
    ]


def test_confirm_cues_checks_revision_and_keeps_existing_cues(review_client, review_root):
    project = store.save(Project(script='Kịch bản.', cues=[Cue(start=0, end=4, text='Cue giữ lại.')],
                                 cues_edited=True, cues_stale=True))
    conflict = review_client.post(f'/api/projects/{project.id}/cues/confirm',
                                  json={'revision': project.revision - 1})
    assert conflict.status_code == 409
    confirmed = review_client.post(f'/api/projects/{project.id}/cues/confirm',
                                   json={'revision': project.revision})
    assert confirmed.status_code == 200, confirmed.text
    result = Project.model_validate(confirmed.json())
    assert result.cues_stale is False
    assert result.cues_edited is True
    assert result.cues == project.cues


def test_duration_change_allows_regeneration_without_stale_and_keeps_clip_caption(review_client, review_root):
    source = Asset(name='Nguồn video', filename='source.mp4', role='source', media='video',
                   duration=5, scenes=[Scene(start=0, end=5)])
    project = store.save(Project(
        script='Câu đầu. Câu thứ hai.', target_duration=5, assets=[source],
        clips=[Clip(asset_id=source.id, scene_id=source.scenes[0].id, duration=5,
                    caption='Phụ đề ghi đè của cảnh.')],
        cues=[Cue(start=0, end=2, text='Cue chỉnh tay.')], cues_edited=True,
    ))
    changed = project.model_copy(deep=True)
    changed.target_duration = 11
    response = review_client.put(f'/api/projects/{project.id}', json=changed.model_dump())
    assert response.status_code == 200, response.text
    duration_changed = Project.model_validate(response.json())
    assert duration_changed.cues_stale is False

    regenerated = review_client.post(f'/api/projects/{project.id}/cues/regenerate',
                                     json={'revision': duration_changed.revision})
    assert regenerated.status_code == 200, regenerated.text
    result = Project.model_validate(regenerated.json())
    assert result.cues[-1].end == pytest.approx(11)
    assert result.clips[0].caption == 'Phụ đề ghi đè của cảnh.'
    assert result.cues_edited is False and result.cues_stale is False
