import base64
import io
import json
import wave

import av
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import audio_assembly, jobs, media, planner, providers, render, store
from backend.main import app
from backend.models import Asset, Clip, Cue, Project, Scene, Settings, TextRegion
from backend.preflight import preflight
from backend.timeline_math import clip_starts, timeline_duration


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    return tmp_path


def wav_bytes(duration=6):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        wav.writeframes(b'\0\0'*int(16000*duration))
    return buffer.getvalue()


def mock_gemini(monkeypatch, segments):
    settings=Settings(ai_profiles=[{'id':'gemini','name':'Gemini','provider':'gemini',
        'model':'configured-model','api_key':'fixture-secret'}],active_ai_profile_id='gemini')
    monkeypatch.setattr(providers,'settings',lambda:settings)
    requests=[]
    def respond(request):
        requests.append(request)
        assert request.url.path.endswith('/configured-model:generateContent')
        payload=json.loads(request.content)
        assert base64.b64decode(payload['contents'][0]['parts'][1]['inlineData']['data']).startswith(b'RIFF')
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({'segments':segments})}]}}]})
    real_client=httpx.Client
    monkeypatch.setattr(providers.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    return requests


def voice_project():
    p=Project(smooth_transitions=True)
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    (root/'voice.wav').write_bytes(wav_bytes())
    voice=Asset(name='Voice',filename='voice.wav',role='voice',media='audio',duration=6,has_audio=True)
    p.assets=[voice];p.voice_id=voice.id
    return p,voice


def test_gemini_only_profile_transcribes_with_safe_telemetry(isolated,monkeypatch):
    p,voice=voice_project()
    calls=mock_gemini(monkeypatch,[{'start':.3,'end':2.8,'text':'Biển xanh.'},{'start':3,'end':5.6,'text':'Núi cao.'}])
    job=jobs.Job(p.id,'transcribe')
    providers.transcribe(p,voice,job)
    assert p.cue_timing=='audio_segments' and p.script=='Biển xanh. Núi cao.'
    assert p.cues[0].start==.3 and p.voice_id==voice.id
    assert len(calls)==1 and job.provider_calls[0]['model']=='configured-model'
    assert 'fixture-secret' not in json.dumps(job.dump())


@pytest.mark.parametrize('segments',[[],[{'start':0,'end':20,'text':'Outside'}],
    [{'start':0,'end':4,'text':'First'},{'start':2,'end':3,'text':'Overlapping'}]])
def test_bad_or_silent_gemini_audio_does_not_modify_project(isolated,monkeypatch,segments):
    p,voice=voice_project();before=p.model_dump()
    mock_gemini(monkeypatch,segments)
    with pytest.raises(ValueError):providers.transcribe(p,voice,jobs.Job(p.id,'transcribe'))
    assert p.model_dump()==before


def test_smooth_audio_plan_matches_sentence_and_never_holds_short_source(isolated,monkeypatch):
    scenes=[Scene(start=0,end=1.7,tags=word,ai_labeled=True) for word in ['biển xanh','núi cao']]
    assets=[Asset(name=word,filename=f'{index}.mp4',media='video',duration=1.7,fps=30,scenes=[scene])
        for index,(word,scene) in enumerate(zip(['biển','núi'],scenes))]
    p=Project(assets=assets,target_duration=6,smooth_transitions=True,cue_timing='audio_segments',
        cues=[Cue(start=0,end=2.8,text='Biển xanh.'),Cue(start=3,end=6,text='Núi cao.')])
    choices={'choices':[{'index':0,'scene_id':scenes[0].id},{'index':1,'scene_id':scenes[1].id}]}
    monkeypatch.setattr(planner,'ai_json',lambda *args,**kwargs:choices)
    planner.plan(p,True,jobs.Job(p.id,'plan'))
    assert timeline_duration(p.clips)==pytest.approx(6)
    assert len(p.clips)==2 and p.clips[1].transition=='crossfade'
    assert clip_starts(p.clips)[1]+p.clips[1].transition_duration/2==pytest.approx(2.8)
    assert [clip.scene_id for clip in p.clips]==[scene.id for scene in scenes]
    assert all(clip.source_start+clip.duration*clip.speed<=1.7 for clip in p.clips)
    assert preflight(p)['ok']


def test_assembly_without_sources_keeps_transcript_and_waits(isolated,monkeypatch):
    p,voice=voice_project();store.save(p)
    mock_gemini(monkeypatch,[{'start':0,'end':5.8,'text':'Nội dung audio.'}])
    with TestClient(app) as client:
        response=client.post(f'/api/projects/{p.id}/assemble-audio')
        assert response.status_code==200
        job=jobs.JOBS[response.json()['id']];job._future.result(timeout=15)
        assert job.status=='done',job.message
        saved=store.read(p.id)
        assert saved.voice_id==voice.id and saved.script=='Nội dung audio.'
        assert saved.target_duration==6 and not saved.clips
        assert 'Thêm video/ảnh' in saved.warnings[0]


def test_upload_requests_automatic_assembly_but_music_does_not(isolated):
    with TestClient(app) as client:
        for role,automatic in [('voice',True),('music',False)]:
            p=store.save(Project())
            response=client.post(f'/api/projects/{p.id}/assets',
                files={'files':('narration.wav',wav_bytes(),'audio/wav')},
                data={'role':role,'auto_assemble':'true'})
            job=jobs.JOBS[response.json()['job']['id']];job._future.result(timeout=15)
            assert job.status=='done'
            assert job.result['auto_assemble'] is automatic
            assert len(store.read(p.id).assets)==1


def test_audio_dropped_in_source_library_is_routed_to_voice(isolated):
    with TestClient(app) as client:
        p=store.save(Project())
        response=client.post(f'/api/projects/{p.id}/assets',
            files={'files':('narration.wav',wav_bytes(),'audio/wav')},
            data={'role':'source','auto_assemble':'true'})
        job=jobs.JOBS[response.json()['job']['id']];job._future.result(timeout=15)
        assert job.status=='done' and job.result['auto_assemble']
        saved=store.read(p.id)
        assert saved.assets[0].role=='voice' and saved.voice_id==saved.assets[0].id


def test_text_regions_reject_unsafe_coordinates():
    for region in [dict(x=.8,y=0,w=.3,h=.1),dict(x=0,y=0,w=float('nan'),h=.1)]:
        with pytest.raises(ValidationError):TextRegion.model_validate(region)


def test_vision_grid_regions_are_normalized_and_still_validated():
    from backend.models import SceneLabels
    raw={'scenes':[{'index':0,'tags':'Phụ đề','text_regions':[{'x':50,'y':800,'w':900,'h':150}]}]}
    normalized=providers._normalized_text_labels(raw)
    SceneLabels.model_validate(normalized)
    assert normalized['scenes'][0]['text_regions'][0]==dict(x=.05,y=.8,w=.9,h=.15)
    assert raw['scenes'][0]['text_regions'][0]['x']==50
    raw['scenes'][0]['text_regions'][0]['w']=1200
    with pytest.raises(ValidationError):SceneLabels.model_validate(providers._normalized_text_labels(raw))


@pytest.mark.parametrize('mode',['cover','blur'])
def test_real_text_cleanup_changes_only_source_region_and_invalidates_cache(isolated,monkeypatch,mode):
    p=Project(aspect='16:9',resolution='720',source_text_mode=mode)
    p.template.caption.enabled=False
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    source=root/'source.mp4'
    media.run_ff(['-f','lavfi','-i','color=blue:s=320x180:r=30',
        '-vf','drawbox=x=32:y=108:w=256:h=36:color=white:t=fill','-t','1',
        '-c:v','libx264','-pix_fmt','yuv420p',source])
    region=TextRegion(x=.1,y=.6,w=.8,h=.2)
    scene=Scene(start=0,end=1,text_regions=[region],text_analyzed=True)
    asset=Asset(name='Text source',filename='source.mp4',media='video',**{k:v for k,v in media.probe(source).items() if k!='media'},scenes=[scene])
    p.assets=[asset];p.clips=[Clip(asset_id=asset.id,scene_id=scene.id,duration=1)]
    monkeypatch.setattr(render,'video_encoder',lambda:('libx264',False,[]))
    result=render.render(p,jobs.Job(p.id,'render'),preview=True)
    with av.open(str(store.project_dir(p.id)/'exports'/result['id']/result['filename'])) as container:
        frame=next(container.decode(video=0)).to_ndarray(format='rgb24')
    # Original source and untouched top retain blue; the white rectangle is hidden/softened.
    assert frame[72,640,2]>180 and frame[72,640,0]<20
    if mode=='cover':assert frame[480,640].mean()<35
    else:assert np.mean(frame[435:455,120:170])<235
    args=(p,p.clips[0],asset,0,[],source,1280,720,1280,720,0,0,True,('libx264',False,[]))
    before=render._clip_cache_path(*args)[0]
    p.clips[0].text_regions_override=True
    assert not render.source_text_regions(p.clips[0],asset)
    assert render._clip_cache_path(*args)[0]!=before
