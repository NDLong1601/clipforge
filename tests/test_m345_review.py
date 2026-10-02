import asyncio
import hashlib
import io
import json
import stat
import threading
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import pytest
from PIL import Image
from starlette.datastructures import UploadFile
from backend import store, jobs, portable, render, template_packages, main
from backend.models import Project, Asset, Scene, Clip, Cue, Layer, Template

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    jobs.JOBS.clear()
    return tmp_path


def test_new_edit_after_redo_undo_should_return_immediate_previous_version():
    p = store.save(Project(name='A'))
    p.name='B'; p=store.save(p)
    p.name='C'; p=store.save(p)
    assert store.undo(p.id).name=='B'
    p=store.redo(p.id)
    assert p.name=='C'
    p.name='D';store.save(p)
    result=store.undo(p.id)
    assert result.name=='C', f'Expected C after D -> Undo, got {result.name}'


def test_cancel_upload_cleans_stage_and_preserves_cancel_exception():
    p=store.save(Project())
    class Disconnected:
        async def is_disconnected(self): return True
    upload_file=UploadFile(filename='sample.png',file=io.BytesIO(b'data'),size=4)
    with pytest.raises(jobs.Cancelled):
        asyncio.run(main.upload(Disconnected(),p.id,[upload_file],'source'))
    assert not any((store.ROOT/'staging').rglob('*.png'))


def test_storage_total_equals_sum_of_categories():
    store.save(Project())
    report=portable.storage_report()
    assert report['total']==sum(report['categories'].values()), report


def test_backup_does_not_block_job_polling_for_other_projects(monkeypatch):
    p=store.save(Project())
    started=threading.Event(); release=threading.Event()
    original_open=zipfile.ZipFile.open
    def blocked_open(zf, name, *args, **kwargs):
        if isinstance(name,zipfile.ZipInfo) and name.filename=='project.json':
            started.set(); assert release.wait(3)
        return original_open(zf,name,*args,**kwargs)
    monkeypatch.setattr(zipfile.ZipFile,'open',blocked_open)
    with ThreadPoolExecutor(max_workers=2) as pool:
        backup_future=pool.submit(portable.create_backup,p.id)
        assert started.wait(1)
        poll=pool.submit(jobs.all_jobs,'another-project')
        try:
            try: result=poll.result(timeout=.2); blocked=False
            except TimeoutError: blocked=True
        finally:
            release.set(); backup_future.result(timeout=3);poll.result(timeout=3)
        assert not blocked, 'Job polling blocked by global lock during backup streaming'


def test_stale_voice_is_accepted_by_next_save_after_script_change():
    voice=Asset(name='Old voice',filename='voice.wav',role='voice',media='audio',duration=1,has_audio=True)
    p=store.save(Project(script='Old script',assets=[voice],voice_id=voice.id))
    first=p.model_copy(deep=True);first.script='New script'
    saved=main._update_project(p.id,first)
    assert saved.voice_id==''
    second=p.model_copy(deep=True);second.script='New script';second.revision=saved.revision;second.music_volume=.2
    persisted=main._update_project(p.id,second)
    # Impact probe: the API legitimately accepts explicit voice selection; the hook must avoid resending it.
    assert persisted.voice_id==voice.id


def project_with_media():
    p=Project(name='Backup roundtrip',script='Xin chào Việt Nam.',target_duration=5,aspect='16:9',resolution='720')
    directory=store.project_dir(p.id)/'assets';directory.mkdir(parents=True)
    Image.new('RGB',(80,50),'green').save(directory/'source.png')
    Image.new('RGBA',(24,24),(255,0,0,200)).save(directory/'logo.png')
    for name,seconds in [('voice.wav',.8),('music.wav',.3)]:
        with wave.open(str(directory/name),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(b'\x00\x10'*int(seconds*8000))
    source=Asset(name='Source',filename='source.png',media='image',scenes=[Scene(start=0,end=1.2)])
    logo=Asset(name='Logo',filename='logo.png',role='overlay',media='image')
    voice=Asset(name='Voice',filename='voice.wav',role='voice',media='audio',duration=.8,has_audio=True)
    music=Asset(name='Music',filename='music.wav',role='music',media='audio',duration=.3,has_audio=True)
    p.assets=[source,logo,voice,music];p.voice_id=voice.id;p.music_id=music.id
    p.clips=[Clip(asset_id=source.id,scene_id=source.scenes[0].id,duration=1.2)]
    p.template=Template(layers=[Layer(kind='image',asset_id=logo.id,w=.1,h=.1)])
    p.cues=[Cue(start=0,end=1.2,text='Xin chào Việt Nam.')];p.cue_timing='manual';p.cues_edited=True
    return store.save(p)


def test_backup_import_fresh_root_render_cache_cleanup_trash_and_template(tmp_path,monkeypatch):
    p=project_with_media()
    p.name='Backup roundtrip edited';p=store.save(p)
    archive=portable.create_backup(p.id,include_history=True)
    old_assets={a.id for a in p.assets}
    fresh_root=tmp_path/'fresh';monkeypatch.setattr(store,'ROOT',fresh_root);monkeypatch.setattr(jobs,'ROOT',fresh_root)
    imported=portable.import_backup(archive)
    assert imported.id!=p.id
    assert not old_assets.intersection(a.id for a in imported.assets)
    assert imported.cues[0].text==p.cues[0].text
    imported=store.undo(imported.id)
    assert imported.name=='Backup roundtrip'
    imported=store.redo(imported.id)
    first=render.render(imported,jobs.Job(imported.id,'render'),preview=True)
    assert (store.project_dir(imported.id)/'exports'/first['id']/first['filename']).is_file()
    assert portable.cleanup_cache()['deleted_bytes']>0
    second=render.render(imported,jobs.Job(imported.id,'render'),preview=True)
    assert (store.project_dir(imported.id)/'exports'/second['id']/second['filename']).is_file()
    package=template_packages.save_package(imported,imported.template)
    assert template_packages.rename_package(package['id'],'New template')['name']=='New template'
    copied=template_packages.duplicate_package(package['id'])
    assert copied['id']!=package['id']
    template_packages.delete_package(package['id'])
    assert template_packages.preview_thumbnail(copied['id'],'1:1').startswith('<svg')
    target=store.save(Project()); target=template_packages.apply_package(target,copied['id']);store.validate_files(target)
    trash=portable.trash_project(imported.id)
    assert portable.list_trash()[0]['id']==trash['id']
    portable.restore_project(trash['id'])
    assert store.read(imported.id).id==imported.id
    trash=portable.trash_project(imported.id);portable.permanently_delete_trash(trash['id'])
    assert not portable.list_trash()


def make_archive(path,member='project.json',kind='project',bad_hash=False,symlink=False,extra=False):
    data=Project().model_dump_json().encode()
    info=zipfile.ZipInfo(member)
    if symlink: info.external_attr=(stat.S_IFLNK|0o777)<<16
    records=[{'path':member,'kind':kind,'size':len(data),'sha256':'0'*64 if bad_hash else hashlib.sha256(data).hexdigest()}]
    with zipfile.ZipFile(path,'w') as z:
        z.writestr(info,data)
        z.writestr('manifest.json',json.dumps({'format':portable.ARCHIVE_FORMAT,'archive_version':1,'schema_version':4,'files':records}))
        if extra:z.writestr('settings.json','secret')
    return path

@pytest.mark.parametrize('options',[{'member':'../project.json'},{'member':'C:/escape/project.json'},{'bad_hash':True},{'symlink':True},{'extra':True}])
def test_bad_zip_rejected_without_publish(tmp_path,options):
    archive=make_archive(tmp_path/'bad.zip',**options)
    with pytest.raises(ValueError):portable.import_backup(archive)
    assert not (tmp_path/'projects').exists()
    assert list((tmp_path/'staging').iterdir())==[]


def test_cleanup_rejected_during_active_job():
    p=store.save(Project());jobs.reserve(p.id,'queued')
    with pytest.raises(ValueError):portable.cleanup_cache()



def test_import_edit_endpoint_keeps_working():
    from fastapi.testclient import TestClient
    p=store.save(Project())
    data = p.model_dump()
    data['name'] = 'Imported edit'
    with TestClient(main.app) as client:
        response = client.post(f'/api/projects/{p.id}/import-edit', json=data)
    assert response.status_code == 200, response.text
    assert store.read(p.id).name == 'Imported edit'


def test_template_thumbnail_shape_matches_renderer():
    from xml.etree import ElementTree as ET
    from backend import planner
    t=planner.presets()[2]
    svg=ET.fromstring(template_packages.template_thumbnail(t))
    rect=svg.findall('{http://www.w3.org/2000/svg}rect')[2]
    assert rect.attrib['fill']==t.layers[0].color


def test_backup_keeps_one_project_consistent_without_blocking_other_writes(monkeypatch):
    p = store.save(Project(name='Snapshot'))
    other = store.save(Project(name='Other'))
    started, release = threading.Event(), threading.Event()
    original_open = zipfile.ZipFile.open

    def blocked_open(zf, name, *args, **kwargs):
        if isinstance(name, zipfile.ZipInfo) and name.filename == 'project.json':
            started.set()
            assert release.wait(5)
        return original_open(zf, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', blocked_open)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(portable.create_backup, p.id)
        assert started.wait(2)
        try:
            with pytest.raises(main.HTTPException) as conflict:
                main._update_project(p.id, p)
            assert conflict.value.status_code == 409
            with pytest.raises(ValueError):
                portable.trash_project(p.id)
            with pytest.raises(ValueError):
                jobs.reserve(p.id, 'voice')
            other.name = 'Other saved during backup'
            saved = pool.submit(main._update_project, other.id, other).result(timeout=2)
            assert saved.name == 'Other saved during backup'
            queued = pool.submit(jobs.reserve, other.id, 'render').result(timeout=2)
            assert pool.submit(jobs.cancel, queued.id).result(timeout=2)['status'] == 'cancelled'
            assert pool.submit(jobs.all_jobs, other.id).result(timeout=2)
        finally:
            release.set()
        archive = future.result(timeout=3)
    assert not jobs.busy(p.id)
    with zipfile.ZipFile(archive) as zf:
        snapshot = json.loads(zf.read('project.json'))
    assert snapshot['name'] == 'Snapshot'
    assert snapshot['revision'] == p.revision


def test_backup_releases_project_reservation_after_zip_write_failure(monkeypatch):
    p = store.save(Project())

    def failed_open(*args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(zipfile.ZipFile, 'open', failed_open)
    with pytest.raises(OSError, match='disk full'):
        portable.create_backup(p.id)
    assert not jobs.busy(p.id)
    assert list((store.ROOT / 'backup_downloads').iterdir()) == []
    p.name = 'Still writable'
    assert main._update_project(p.id, p).name == 'Still writable'


def test_upload_cleanup_survives_failure_to_persist_cancelled_status(monkeypatch):
    p = store.save(Project())
    original_persist = jobs._persist

    def fail_cancelled_status(job):
        if job.status == 'cancelled':
            raise OSError('disk full')
        original_persist(job)

    class Disconnected:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(jobs, '_persist', fail_cancelled_status)
    upload_file = UploadFile(filename='sample.png', file=io.BytesIO(b'data'), size=4)
    with pytest.raises(jobs.Cancelled):
        asyncio.run(main.upload(Disconnected(), p.id, [upload_file], 'source'))
    assert not any((store.ROOT / 'staging').rglob('*.png'))
    assert not jobs.busy(p.id)


def test_upload_disk_error_is_persisted_without_partial_media(monkeypatch):
    p = store.save(Project())
    original_run_sync = main.anyio.to_thread.run_sync

    async def fail_write(callback, *args, **kwargs):
        if getattr(callback, '__name__', '') == 'write':
            raise OSError('disk full')
        return await original_run_sync(callback, *args, **kwargs)

    class Connected:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(main.anyio.to_thread, 'run_sync', fail_write)
    upload_file = UploadFile(filename='sample.png', file=io.BytesIO(b'data'), size=4)
    with pytest.raises(OSError, match='disk full'):
        asyncio.run(main.upload(Connected(), p.id, [upload_file], 'source'))
    record = json.loads(next((store.ROOT / 'jobs').glob('*.json')).read_text(encoding='utf-8'))
    assert record['status'] == 'error'
    assert record['message'] == 'disk full'
    assert not any((store.ROOT / 'staging').rglob('*.png'))
    assert store.read(p.id).assets == []


def test_legacy_snapshot_names_order_and_prune_by_revision():
    p = store.save(Project(name='A'))
    for name in ('B', 'C', 'D'):
        p.name = name
        p = store.save(p)
    directory = store.project_dir(p.id) / 'history'
    (directory / '00000001.json').rename(directory / '20261001T000000000000Z-00000001.json')
    assert store.undo(p.id).name == 'C'
    assert store.undo(p.id).name == 'B'
    assert store.redo(p.id).name == 'C'
    assert store.redo(p.id).name == 'D'
    p = store.read(p.id)
    for index in range(30):
        p.name = f'Edit {index}'
        p = store.save(p)
    assert len(list(directory.glob('*.json'))) == 25
    assert store.undo(p.id).name == 'Edit 28'
    assert store.undo(p.id).name == 'Edit 27'
    assert store.redo(p.id).name == 'Edit 28'
    assert not (directory / '20261001T000000000000Z-00000001.json').exists()


def test_optional_exports_history_and_secrets_in_backup(tmp_path, monkeypatch):
    p = project_with_media()
    export_id = 'd' * 16
    export_dir = store.project_dir(p.id) / 'exports' / export_id
    export_dir.mkdir(parents=True)
    (export_dir / 'video.mp4').write_bytes(b'export payload')
    p.exports = [{'id': export_id, 'filename': 'video.mp4'}]
    p = store.save(p)
    (store.ROOT / 'settings.json').write_text('{"ai_key":"do-not-archive"}', encoding='utf-8')
    plain = portable.create_backup(p.id)
    archive = portable.create_backup(p.id, include_exports=True, include_history=True)
    with zipfile.ZipFile(plain) as zf:
        assert not any(name.startswith(('history/', 'exports/')) for name in zf.namelist())
    with zipfile.ZipFile(archive) as zf:
        assert f'exports/{export_id}/video.mp4' in zf.namelist()
        assert any(name.startswith('history/') for name in zf.namelist())
        assert 'settings.json' not in zf.namelist()
        assert all(b'do-not-archive' not in zf.read(name) for name in zf.namelist())
    fresh = tmp_path / 'fresh'
    monkeypatch.setattr(store, 'ROOT', fresh)
    imported = portable.import_backup(archive)
    assert imported.exports[0]['id'] == export_id
    assert (store.project_dir(imported.id) / 'exports' / export_id / 'video.mp4').read_bytes() == b'export payload'
