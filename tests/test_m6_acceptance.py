from __future__ import annotations

import hashlib
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import jobs, media, render, store
from backend.main import app
from backend.models import Asset, Clip, Cue, Project
from backend.preflight import preflight
from backend.timeline_math import timeline_duration
from tests.fixtures.factory import make_media_project
from tests.generate_corpus import generate
from tools.package_release import ARCHIVE_ROOT, build_release


@pytest.fixture
def m7_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    jobs.JOBS.clear()
    return tmp_path


def test_schema_v4_migration_is_idempotent_and_preserves_ids():
    project = Project()
    project.template.caption.font_family = 'DejaVu Sans'
    project.template.caption.safe_area = 'reels'
    data = project.model_dump()
    data['schema_version'] = 4
    data.pop('source_volume')
    data['template'].pop('transition_duration')
    data['template']['caption'].pop('font_family')
    data['template']['caption'].pop('safe_area')

    first = store._migrate_project(data)
    second = store._migrate_project(first)

    assert first == second
    assert first['id'] == project.id and first['schema_version'] == 5
    assert first['source_volume'] == 0
    assert first['template']['transition_duration'] == .4
    assert first['template']['caption']['font_family'] == 'Arial'
    assert first['template']['caption']['safe_area'] == 'standard'


def test_crossfade_uses_one_project_clock_and_preflight_checks_handles():
    source = Asset(name='Nguồn', filename='source.mp4', role='source', media='video', duration=5,
                   width=240, height=320, fps=30, has_audio=True)
    first = Clip(asset_id=source.id, source_start=0, duration=2.5)
    second = Clip(asset_id=source.id, source_start=2.5, duration=2.5,
                  transition='crossfade', transition_duration=.5)
    project = Project(assets=[source], clips=[first, second])
    assert timeline_duration(project.clips) == pytest.approx(4.5)
    assert preflight(project)['ok']

    second.source_start = 2.6
    report = preflight(project)
    assert any(issue['code'] == 'transition.handle_missing' and issue['severity'] == 'error'
               for issue in report['issues'])


def test_real_crossfade_render_has_expected_size_duration_and_source_audio(m7_root):
    project = Project(name='Crossfade nghiệm thu', target_duration=5, resolution='720', source_volume=.4)
    source_path = store.project_dir(project.id) / 'assets' / 'source.mp4'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    media.run_ff([
        '-f', 'lavfi', '-i', 'testsrc2=size=240x320:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=48000', '-t', '5',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', source_path,
    ])
    source = Asset(name='Nguồn có âm thanh', filename=source_path.name,
                   role='source', **media.probe(source_path))
    project.assets = [source]
    project.clips = [
        Clip(asset_id=source.id, source_start=0, duration=2.5),
        Clip(asset_id=source.id, source_start=2.5, duration=2.5,
             transition='crossfade', transition_duration=.5),
    ]
    project.cues = [Cue(start=2, end=2.2, text='Đúng mốc overlap')]
    project.template.caption.enabled = True
    project = store.save(project)

    rendered = render.render(project, jobs.Job(project.id, 'm7-crossfade'), preview=True)
    output = store.project_dir(project.id) / 'exports' / rendered['id'] / rendered['filename']
    info = media.probe(output)

    assert (info['width'], info['height']) == (720, 1280)
    assert info['has_audio'] and info['duration'] == pytest.approx(4.5, abs=.15)
    subtitles = (output.parent / 'captions.srt').read_text(encoding='utf-8-sig')
    assert '00:00:02,000 --> 00:00:02,200\nĐúng mốc overlap' in subtitles


def test_vfr_and_rotated_mov_analysis_keeps_bounds_and_rotation(m7_root):
    corpus = m7_root / 'corpus'
    manifest = generate(corpus, long_seconds=6)
    assert manifest['checks']['rotated_mov_display_degrees'] == 90
    assert manifest['checks']['vfr_distinct_frame_intervals'] >= 2

    pid = Project().id
    assets_dir = store.project_dir(pid) / 'assets'
    assets_dir.mkdir(parents=True)
    analyzed = {}
    for name in ('variable_framerate.mp4', 'rotated_metadata.mov'):
        source = corpus / name
        destination = assets_dir / name
        shutil.copy2(source, destination)
        asset = Asset(name=name, filename=name, role='source', **media.probe(destination))
        media.analyze(pid, asset)
        assert asset.scenes
        assert all(0 <= scene.start < scene.end <= asset.duration + .01 for scene in asset.scenes)
        assert all((store.project_dir(pid) / 'thumbs' / scene.thumbnail).is_file()
                   for scene in asset.scenes)
        analyzed[name] = asset

    rotated = analyzed['rotated_metadata.mov']
    with Image.open(store.project_dir(pid) / 'thumbs' / rotated.scenes[0].thumbnail) as thumbnail:
        assert thumbnail.height > thumbnail.width


def test_ai_preview_token_is_revision_bound_and_one_time(m7_root):
    project = make_media_project(include_voice=False, include_music=False)
    client = TestClient(app)
    payload = {'revision': project.revision, 'music_volume': .25}
    preview = client.post(f'/api/projects/{project.id}/assistant-preview', json=payload)
    assert preview.status_code == 200 and preview.json()['can_apply']
    token = preview.json()['preview_token']
    assert token

    current = store.read(project.id).model_dump()
    current['name'] = 'Đã đổi revision'
    changed = client.put(f'/api/projects/{project.id}', json=current)
    assert changed.status_code == 200
    commit = {**payload, 'preview_token': token}
    stale = client.post(f'/api/projects/{project.id}/apply-ai', json=commit)
    replay = client.post(f'/api/projects/{project.id}/apply-ai', json=commit)

    assert stale.status_code == 409 and replay.status_code == 409
    saved = store.read(project.id)
    assert saved.name == 'Đã đổi revision' and saved.music_volume == project.music_volume


def test_release_archive_includes_runtime_and_verified_manifest(tmp_path):
    output = tmp_path / 'ClipForge source.zip'
    archive_path, digest = build_release(output)

    assert archive_path == output and output.with_suffix('.zip.sha256').is_file()
    assert digest in output.with_suffix('.zip.sha256').read_text(encoding='ascii')
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        prefix = ARCHIVE_ROOT + '/'
        assert prefix + 'MO_CLIPFORGE.bat' in names
        assert prefix + 'frontend/dist/index.html' in names
        assert prefix + 'frontend/package-lock.json' in names
        assert prefix + 'requirements-lock.txt' in names
        assert not any('/data/' in name or '/.venv/' in name or '/node_modules/' in name for name in names)
        manifest = archive.read(prefix + 'RELEASE-MANIFEST.sha256').decode('utf-8').splitlines()
        for record in manifest:
            expected, relative = record.split('  ', 1)
            assert expected == hashlib.sha256(archive.read(prefix + relative)).hexdigest()
