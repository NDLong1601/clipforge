import os, json, threading, time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import store, media, planner, render, providers, jobs
from backend.main import app
from backend.models import Project, Clip, Cue, Layer, Viewport, Asset, Settings

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    monkeypatch.setattr(jobs,'ROOT',tmp_path)
    return tmp_path

@pytest.fixture
def client(isolated):
    return TestClient(app)

def make_project():
    p=store.save(Project(name='Kiểm thử',target_duration=6,resolution='720',script='Một cảnh ngắn. Câu chuyện tiếp tục.',template=planner.presets()[0]))
    d=store.project_dir(p.id)/'assets';d.mkdir(parents=True,exist_ok=True)
    path=d/'test.mp4'
    media.run_ff(['-f','lavfi','-i','testsrc2=size=240x320:rate=30','-t','6','-an','-c:v','libx264','-pix_fmt','yuv420p',path])
    a=media.register(p.id,path,'Nguồn thử.mp4','source');media.analyze(p.id,a);p.assets.append(a)
    music=d/'music.wav';media.run_ff(['-f','lavfi','-i','sine=frequency=220:sample_rate=48000','-t','1',music])
    m=media.register(p.id,music,'Nhạc.wav','music');p.assets.append(m);p.music_id=m.id
    return store.save(p)

def test_api_revision_security_and_undo(client):
    p=client.post('/api/projects',json={'name':'Dự án tiếng Việt'}).json();pid=p['id']
    changed=dict(p,name='Tên mới')
    assert client.put('/api/projects/'+pid,json=changed).status_code==200
    assert client.put('/api/projects/'+pid,json=changed).status_code==409
    assert client.post('/api/projects/'+pid+'/undo').json()['name']=='Dự án tiếng Việt'
    assert client.post('/api/projects',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/health',headers={'Host':'evil.example'}).status_code==400
    assert client.get('/api/projects/not-valid').status_code==400

def test_settings_redaction(client):
    r=client.put('/api/settings',json={'ai_key':'test-secret-not-real'})
    assert r.status_code==200 and r.json()['ai_key']=='••••••••'
    assert 'test-secret-not-real' not in client.get('/api/settings').text
    assert client.put('/api/settings',json={'ai_base_url':'http://example.com'}).status_code==400

def test_plan_caption_alignment_and_duration(isolated):
    p=make_project();p=planner.plan(p,False,jobs.Job(p.id,'test'))
    assert abs(sum(c.duration for c in p.clips)-6)<.01
    assert all(1.8<=c.duration<=3.2 for c in p.clips)
    assert len(p.cues)==2
    groups=planner.caption_groups(p.cues,3)
    assert groups[-1][-1].end==pytest.approx(6)
    assert all(g[-1].end>g[0].start for g in groups)

def test_render_real_video_with_audio_overlays_and_captions(isolated):
    p=make_project();p=planner.plan(p,False,jobs.Job(p.id,'test'))
    d=store.project_dir(p.id)/'assets';voice=d/'voice.wav'
    media.run_ff(['-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','5.8',voice])
    v=media.register(p.id,voice,'Voice.wav','voice');p.assets.append(v);p.voice_id=v.id
    p.template.viewport=Viewport(x=.05,y=.1,w=.9,h=.8)
    p.template.layers.append(Layer(kind='text',text='Tiếng Việt có dấu',x=.05,y=.02,w=.9,h=.06,size=40,animation='fade'))
    from PIL import Image
    logo=d/'logo.png';Image.new('RGBA',(80,80),(180,240,100,200)).save(logo)
    image=media.register(p.id,logo,'Logo.png','overlay');p.assets.append(image)
    p.template.layers.append(Layer(kind='image',asset_id=image.id,x=.8,y=.1,w=.1,h=.1,animation='slide'))
    p.clips[0].transition='fade';p.template.caption.karaoke=True
    result=render.render(p,jobs.Job(p.id,'render'),preview=True)
    target=store.project_dir(p.id)/'exports'/result['id']
    info=media.probe(target/result['filename'])
    assert info['width']==720 and info['height']==1280
    assert info['has_audio'] and abs(info['duration']-6)<.15
    assert 'Một cảnh' in (target/'captions.srt').read_text(encoding='utf-8-sig')
    assert (target/'project.json').exists()

def test_template_timing_and_voice_protection(isolated):
    p=make_project();p.mode='template';p.template.slot_durations=[1,2,1]
    p=planner.plan(p,False,jobs.Job(p.id,'test'))
    assert [c.duration for c in p.clips]==[1.5,3,1.5]
    p.voice_id=p.music_id;store.get_asset(p,p.voice_id).duration=9
    with pytest.raises(ValueError,match='ngắn hơn voice'):planner.validate_timeline(p)

def test_ai_adapters_and_reject_invalid_actions(client,monkeypatch):
    p=make_project();p=planner.plan(p,False,jobs.Job(p.id,'test'));store.save(p)
    pid=p.id
    for asset in p.assets:
        for scene in asset.scenes:scene.ai_labeled=True
    monkeypatch.setattr(planner,'ai_json',lambda *args,**kwargs:{'choices':[{'index':0,'scene_id':p.assets[0].scenes[0].id}]})
    result=planner.plan(p,True,jobs.Job(pid,'test'));assert result.clips[0].scene_id==p.assets[0].scenes[0].id
    store.save(result)
    before=store.read(pid)
    r=client.post(f'/api/projects/{pid}/assistant-preview',json={
        'revision':before.revision,'clip_changes':[{'id':p.clips[0].id,'asset_id':'missing'}]})
    assert r.status_code==400
    assert store.read(pid).model_dump()==before.model_dump()
    preview=client.post(f'/api/projects/{pid}/assistant-preview',json={
        'revision':before.revision,'music_volume':.2})
    assert preview.status_code==200 and preview.json()['can_apply']
    applied=client.post(f'/api/projects/{pid}/apply-ai',json={
        'revision':before.revision,'music_volume':.2,
        'preview_token':preview.json()['preview_token']})
    assert applied.status_code==200 and applied.json()['music_volume']==.2

def test_subtitle_override_has_no_duplicate_overlap(isolated):
    p=Project(clips=[Clip(asset_id='a',duration=3,caption='Mới'),Clip(asset_id='b',duration=3)],cues=[Cue(start=0,end=6,text='Cũ')])
    d=store.ROOT;render.subtitles(p,720,1280,d)
    srt=(d/'captions.srt').read_text(encoding='utf-8-sig')
    assert '00:00:00,000 --> 00:00:03,000\nMới' in srt
    assert '00:00:03,000 --> 00:00:06,000\nCũ' in srt

def test_cancel_job_without_saving(isolated):
    p=store.save(Project())
    started=threading.Event()
    def operation(job):
        started.set()
        while True:job.check();time.sleep(.02)
    result=jobs.submit(p.id,'test-cancel',operation);started.wait(2)
    jobs.JOBS[result['id']].cancelled.set()
    for _ in range(100):
        if jobs.JOBS[result['id']].status=='cancelled':break
        time.sleep(.02)
    assert jobs.JOBS[result['id']].status=='cancelled'

def test_upload_filename_cannot_escape(client,tmp_path):
    from PIL import Image
    import io
    b=io.BytesIO();Image.new('RGB',(40,60),'green').save(b,format='PNG')
    p=client.post('/api/projects',json={}).json()
    r=client.post('/api/projects/'+p['id']+'/assets',files={'files':('../../evil.png',b.getvalue(),'image/png')},data={'role':'overlay'})
    assert r.status_code==200
    job=r.json()['job']
    for _ in range(100):
        status=client.get('/api/jobs?pid='+p['id']).json()
        found=next((item for item in status if item['id']==job['id']),None)
        if found and found['status'] in ('done','error','cancelled'):break
        time.sleep(.02)
    assert found['status']=='done',found
    a=client.get('/api/projects/'+p['id']).json()['assets'][0]
    assert '/' not in a['filename'] and '\\' not in a['filename']

def test_ai_protocol_contract(isolated,monkeypatch):
    import httpx
    real_client=httpx.Client
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"message":"ok"}'}}]})
    monkeypatch.setattr(providers.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
    store.save_settings({'ai_key':'not-a-real-key','ai_model':'example-model'})
    result=providers.ai_json('Return JSON')
    assert result['message']=='ok'
    body=json.loads(seen[0].content)
    assert body['response_format']=={'type':'json_object'}
    assert body['model']=='example-model'
    assert str(seen[0].url).endswith('/v1/chat/completions')

def test_multiple_api_keys_are_masked_and_preserved(client):
    profiles=[
        {'id':'openai-1','name':'OpenAI chính','provider':'openai','api_key':'key-one-fake',
         'model':'gpt-4o-mini','base_url':'','enabled':True},
        {'id':'gemini-2','name':'Gemini dự phòng','provider':'gemini','api_key':'key-two-fake',
         'model':'gemini-2.5-flash','base_url':'','enabled':True},
    ]
    payload={'ai_profiles':profiles,'active_ai_profile_id':'openai-1','ai_auto_fallback':True}
    response=client.put('/api/settings',json=payload)
    assert response.status_code==200
    public=response.json()
    assert [p['api_key'] for p in public['ai_profiles']]==['••••••••','••••••••']
    assert 'key-one-fake' not in client.get('/api/settings').text
    public['ai_profiles'][1]['model']='gemini-2.5-pro'
    assert client.put('/api/settings',json=public).status_code==200
    saved=store.settings()
    assert [p.api_key for p in saved.ai_profiles]==['key-one-fake','key-two-fake']
    assert saved.ai_profiles[1].model=='gemini-2.5-pro'
    public['ai_profiles']=public['ai_profiles'][1:]
    public['active_ai_profile_id']='gemini-2'
    assert client.put('/api/settings',json=public).status_code==200
    assert [p.id for p in store.settings().ai_profiles]==['gemini-2']
    public['ai_profiles'][0]['provider']='compatible'
    public['ai_profiles'][0]['base_url']='http://example.com/v1'
    assert client.put('/api/settings',json=public).status_code==400

def test_legacy_key_migrates_without_stale_copy(isolated):
    store.save_settings({'ai_key':'legacy-fake','ai_model':'gpt-4o-mini'})
    public=store.public_settings()
    assert public['ai_profiles'][0]['id']=='legacy'
    assert public['ai_profiles'][0]['api_key']=='••••••••'
    store.save_settings(public)
    saved=store.settings()
    assert saved.ai_key==''
    assert saved.ai_profiles[0].api_key=='legacy-fake'
    store.save_settings({'ai_profiles':[],'active_ai_profile_id':''})
    assert store.settings().ai_profiles==[]
    assert store.settings().ai_key==''

def test_ai_fallback_and_manual_switch(isolated,monkeypatch):
    import httpx
    real_client=httpx.Client
    seen=[]
    def respond(request):
        seen.append(request)
        if 'api.openai.com' in str(request.url):
            return httpx.Response(429,json={'error':'rate limit'})
        return httpx.Response(200,json={'choices':[{'message':{'content':'```json\n{"message":"ok"}\n```'}}]})
    monkeypatch.setattr(providers.httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
    store.save_settings({'ai_profiles':[
        {'id':'one','name':'OpenAI','provider':'openai','api_key':'fake-one','model':'bad-model','base_url':'','enabled':True},
        {'id':'two','name':'Gemini','provider':'gemini','api_key':'fake-two','model':'gemini-2.5-flash','base_url':'','enabled':True},
    ],'active_ai_profile_id':'one','ai_auto_fallback':True})
    result,route=providers.ai_json('Return JSON',return_route=True)
    assert result['message']=='ok' and route['id']=='two' and route['fallback_used']
    assert len(seen)==3
    assert json.loads(seen[0].content)['response_format']=={'type':'json_object'}
    assert json.loads(seen[1].content)['response_format']=={'type':'json_object'}
    assert 'response_format' not in json.loads(seen[2].content)
    assert 'generativelanguage.googleapis.com' in str(seen[2].url)
    assert seen[2].headers['Authorization']=='Bearer fake-two'
    seen.clear()
    store.save_settings({'active_ai_profile_id':'two'})
    result,route=providers.ai_json('Return JSON',return_route=True)
    assert result['message']=='ok' and not route['fallback_used'] and len(seen)==1
    seen.clear()
    store.save_settings({'active_ai_profile_id':'one','ai_auto_fallback':False})
    with pytest.raises(ValueError,match='HTTP 429'):
        providers.ai_json('Return JSON')
    assert len(seen)==2

def test_template_inference_contract(isolated,monkeypatch):
    p=make_project();a=p.assets[0]
    monkeypatch.setattr(providers,'ai_json',lambda *args,**kwargs:{'name':'Mẫu AI','viewport':{'x':0,'y':.1,'w':1,'h':.8},'layers':[{'kind':'text','text':'{title}'}]})
    t=providers.infer_template(p,a,jobs.Job(p.id,'template'))
    assert t.name=='Mẫu AI' and len(t.slot_durations)==len(a.scenes)
    monkeypatch.setattr(providers,'ai_json',lambda *args,**kwargs:{'viewport':{'x':.9,'w':.9}})
    with pytest.raises(ValueError,match='bố cục không hợp lệ'):providers.infer_template(p,a,jobs.Job(p.id,'template'))
