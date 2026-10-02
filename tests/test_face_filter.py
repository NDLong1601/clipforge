import math

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import face_filter, jobs, media, planner, render, store
from backend.main import app
from backend.models import Asset, Clip, Cue, Project, Scene
from backend.preflight import preflight
from backend.timeline_math import clip_starts, timeline_duration


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    monkeypatch.setattr(jobs,'ROOT',tmp_path)
    return tmp_path


class ColorDetector:
    def has_face(self,image):return image[:,:,2].mean()>100


def image_project():
    p=Project(target_duration=6,script='Sản phẩm B5. Sản phẩm B5.',avoid_faces=True)
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    for name,color in [('face','red'),('clean','blue')]:
        Image.new('RGB',(120,180),color).save(root/f'{name}.png')
        scene=Scene(start=0,end=3,tags='sản phẩm B5',ai_labeled=True)
        p.assets.append(Asset(name=name,filename=f'{name}.png',media='image',width=120,height=180,scenes=[scene]))
    p.clips=[Clip(asset_id=a.id,scene_id=a.scenes[0].id,duration=3,title='Sản phẩm B5') for a in p.assets]
    return p


def test_existing_project_defaults_to_filter_off():
    data=Project().model_dump();data.pop('avoid_faces')
    assert Project.model_validate(data).avoid_faces is False


def test_cache_invalidates_on_source_change_and_unreadable_is_unknown(isolated):
    p=image_project();asset=p.assets[0];detector=ColorDetector()
    assert face_filter.scan_range(p.id,asset,0,3,detector=detector)['status']=='present'
    path=store.asset_path(p.id,asset);Image.new('RGB',(120,180),'blue').save(path)
    assert face_filter.scan_range(p.id,asset,0,3,detector=detector)['status']=='clear'
    path.write_bytes(b'corrupt')
    assert face_filter.scan_range(p.id,asset,0,3,detector=detector)['status']=='unknown'


def test_scan_checks_inside_video_and_crop(isolated):
    p=Project();root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    path=root/'source.mp4'
    media.run_ff(['-f','lavfi','-i','color=blue:s=160x240:r=30',
        '-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='between(t,0.25,0.45)'",
        '-t','1','-c:v','libx264','-pix_fmt','yuv420p',path])
    asset=Asset(name='source',filename=path.name,**media.probe(path))
    assert face_filter.scan_range(p.id,asset,0,1,detector=ColorDetector())['status']=='present'
    assert face_filter.scan_range(p.id,asset,.6,1,detector=ColorDetector())['status']=='clear'
    image=np.zeros((200,100,3),dtype=np.uint8);image[:50,:,2]=255
    assert not ColorDetector().has_face(image)
    assert face_filter._visible(image,[1,.5,1]).shape==(100,100,3)
    assert not face_filter._visible(image,[1,.5,1]).any()


def test_bundled_detector_runs_without_network(isolated):
    p=image_project();asset=p.assets[1]
    assert face_filter.scan_range(p.id,asset,0,3)['status']=='clear'


def test_face_filter_preserves_timing_voice_template_and_clean_clip(isolated,monkeypatch):
    monkeypatch.setattr(face_filter,'Detector',ColorDetector)
    p=image_project();before=p.model_copy(deep=True);starts=clip_starts(p.clips)
    face_filter.filter_timeline(p,jobs.Job(p.id,'filter-faces'))
    assert p.clips[0].asset_id==p.assets[1].id
    assert p.clips[1]==before.clips[1]
    assert [c.id for c in p.clips]==[c.id for c in before.clips]
    assert starts==clip_starts(p.clips) and timeline_duration(p.clips)==6
    assert p.template==before.template and p.voice_id==before.voice_id
    assert preflight(p)['ok']
    # Editing the physical source invalidates its proof and blocks exporting it.
    store.asset_path(p.id,p.assets[1]).write_bytes(b'changed-source')
    assert not preflight(p)['ok']


def test_locked_conflict_and_no_safe_pool_leave_clips_unchanged(isolated,monkeypatch):
    monkeypatch.setattr(face_filter,'Detector',ColorDetector)
    p=image_project();p.clips[0].locked=True;before=[c.model_dump() for c in p.clips]
    with pytest.raises(ValueError,match='đã khóa'):face_filter.filter_timeline(p,jobs.Job(p.id,'filter-faces'))
    assert [c.model_dump() for c in p.clips]==before
    p.clips[0].locked=False;p.assets[1].deleted=True;p.clips=p.clips[:1];before=p.clips[0].model_dump()
    with pytest.raises(ValueError,match='Không đủ cảnh'):face_filter.filter_timeline(p,jobs.Job(p.id,'filter-faces'))
    assert p.clips[0].model_dump()==before


def test_ai_cannot_choose_excluded_face_and_unknown_never_falls_back(isolated,monkeypatch):
    monkeypatch.setattr(face_filter,'Detector',ColorDetector)
    p=image_project();face=p.assets[0].scenes[0].id
    prompts=[]
    def choose(prompt,**kwargs):
        prompts.append(prompt);return {'choices':[{'index':0,'scene_id':face},{'index':1,'scene_id':face}]}
    monkeypatch.setattr(planner,'ai_json',choose)
    planner.plan(p,True,jobs.Job(p.id,'plan'))
    assert all(c.asset_id==p.assets[1].id for c in p.clips)
    assert face not in prompts[0]
    store.asset_path(p.id,p.assets[1]).write_bytes(b'bad')
    with pytest.raises(ValueError,match='Chưa có cảnh'):planner.plan(p,False,jobs.Job(p.id,'plan'))


def test_checkbox_setting_persists_and_filter_job_commits_atomically(isolated,monkeypatch):
    monkeypatch.setattr(face_filter,'Detector',ColorDetector)
    p=image_project();p.avoid_faces=False;store.save(p)
    with TestClient(app) as client:
        data=client.get(f'/api/projects/{p.id}').json();data['avoid_faces']=True
        assert client.put(f'/api/projects/{p.id}',json=data).status_code==200
        assert not client.get(f'/api/projects/{p.id}/preflight').json()['ok']
        response=client.post(f'/api/projects/{p.id}/filter-faces')
        job=jobs.JOBS[response.json()['id']];job._future.result(timeout=15)
        assert job.status=='done',job.message
        saved=store.read(p.id)
        assert saved.avoid_faces and saved.clips[0].asset_id==saved.assets[1].id
        assert client.get(f'/api/projects/{p.id}/preflight').json()['ok']


@pytest.mark.parametrize('tracks',['voice','music','voice_music'])
def test_render_aac_has_real_time_sample_clock(isolated,monkeypatch,tracks):
    p=Project(resolution='720',target_duration=5)
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    Image.new('RGB',(120,180),'blue').save(root/'source.png')
    source=Asset(name='image',filename='source.png',media='image',width=120,height=180)
    p.assets=[source];p.clips=[Clip(asset_id=source.id,duration=5)];p.template.caption.enabled=False
    if tracks=='voice':
        # Fractional fast cuts exercise the concatenated H.264 input that caused
        # repeated AAC timestamps when video/audio were encoded in one graph.
        durations=[.937766]*5+[5-.937766*5]
        p.clips=[Clip(asset_id=source.id,duration=duration) for duration in durations]
    for role in ('voice','music'):
        if role not in tracks:continue
        path=root/f'{role}.wav'
        media.run_ff(['-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','5',path])
        asset=Asset(name=role,filename=path.name,role=role,**media.probe(path))
        p.assets.append(asset);setattr(p,f'{role}_id',asset.id)
    monkeypatch.setattr(render,'video_encoder',lambda:('libx264',False,[]))
    result=render.render(p,jobs.Job(p.id,'render'),preview=True)
    path=store.project_dir(p.id)/'exports'/result['id']/result['filename']
    with av.open(str(path)) as container:
        pts=[packet.pts for packet in container.demux(audio=0) if packet.size and packet.pts is not None]
    assert len(pts)==math.ceil(5*48000/1024)+1
    assert all(right-left==1024 for left,right in zip(pts,pts[1:]))
    with av.open(str(path)) as container:
        samples=sum(frame.samples for frame in container.decode(audio=0))
    assert 5*48000<=samples<5*48000+1024
