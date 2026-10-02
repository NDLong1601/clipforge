"""Regression coverage for M6-M7 review findings; isolated media and mocked AI."""
from pathlib import Path
import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageFont

from backend import font_manager, jobs, media, planner, providers, render, store
from backend.main import app
from backend.models import Asset, Clip, Project, Scene, Cue, Layer, CaptionStyle
from backend.preflight import preflight
from backend.timeline_math import clip_starts
from tests.fixtures.factory import make_media_project


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    jobs.JOBS.clear()


@pytest.mark.parametrize('rotation',[90,270])
def test_rotated_thumbnail_matches_ffmpeg_display_direction(tmp_path,rotation):
    project = Project()
    root = store.project_dir(project.id) / 'assets'
    root.mkdir(parents=True)
    base, rotated = root / 'base.mp4', root / 'rotated.mov'
    media.run_ff(['-f', 'lavfi', '-i', 'color=red:size=320x180:rate=30',
                  '-vf', 'drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill',
                  '-t', '2', '-c:v', 'libx264', '-preset', 'ultrafast', base])
    media.run_ff(['-display_rotation', str(rotation), '-i', base, '-c', 'copy', rotated])
    asset = Asset(name='Rotated', filename=rotated.name, **media.probe(rotated))
    media.analyze(project.id, asset)
    actual = store.project_dir(project.id) / 'thumbs' / asset.scenes[0].thumbnail
    reference = tmp_path / 'ffmpeg_display.png'
    media.thumbnail(rotated, reference, 1)
    with Image.open(actual) as image:
        observed = np.asarray(image.convert('RGB'), dtype=float)
    with Image.open(reference) as image:
        expected = np.asarray(image.convert('RGB').resize((observed.shape[1], observed.shape[0])), dtype=float)
    error = float(np.mean(np.abs(observed - expected)))
    print({'rotation': rotation, 'mean_rgb_error': round(error, 2),
           'actual_top': observed[:30].mean(axis=(0, 1)).round().tolist(),
           'reference_top': expected[:30].mean(axis=(0, 1)).round().tolist()})
    assert error < 10, 'Analysis thumbnail is rotated opposite to the displayed/exported MOV'
    original_ids=[scene.id for scene in asset.scenes]
    for scene in asset.scenes:scene.ai_labeled=True
    marker=actual.parent/f'{asset.id}.scenes.json'
    marker.unlink()
    Image.new('RGB',(180,320),'green').save(actual)
    assert media.refresh_scene_thumbnails(project.id,asset)
    assert [scene.id for scene in asset.scenes]==original_ids
    assert all(not scene.ai_labeled for scene in asset.scenes)
    with Image.open(actual) as image:
        repaired=np.asarray(image.convert('RGB'),dtype=float)
    assert float(np.mean(np.abs(repaired-expected)))<10
    assert media.refresh_scene_thumbnails(project.id,asset) is False


@pytest.mark.parametrize('stage',['clip','join','final'])
def test_render_recovers_from_hardware_failure(monkeypatch,stage):
    p = Project(resolution='720')
    path = store.project_dir(p.id) / 'assets' / 'still.png'
    path.parent.mkdir(parents=True)
    Image.new('RGB', (160, 90), 'red').save(path)
    a = Asset(name='Still', filename=path.name, **media.probe(path))
    p.assets = [a]
    p.clips = [Clip(asset_id=a.id, duration=1.5),
               Clip(asset_id=a.id,duration=1.5,transition='crossfade',transition_duration=.5)]
    p.template.caption.enabled = False
    p = store.save(p)
    monkeypatch.setattr(render, '_VIDEO_ENCODER', ('h264_qsv', True, []))
    real_run = render.run_ff
    attempts = []
    failed=[]

    def failing_hardware(args, **kwargs):
        if '-c:v' in args:
            encoder = args[args.index('-c:v') + 1]
            destination=Path(args[-1]).name
            attempts.append((destination,encoder))
            should_fail=(stage=='clip' and destination.startswith('clip_') or
                         stage=='join' and destination=='joined.mp4' or
                         stage=='final' and destination=='final_video.mp4')
            if encoder == 'h264_qsv' and should_fail:
                Path(kwargs['cwd'],destination).write_bytes(b'partial hardware output')
                failed.append(destination)
                raise ValueError('Injected hardware device unavailable after successful probe')
            # Simulate successful hardware stages independently of the CI driver.
            if encoder=='h264_qsv':
                args=list(args);args[args.index('-c:v')+1]='libx264'
                args[-1:-1]=['-preset','ultrafast','-threads','2']
        return real_run(args, **kwargs)

    monkeypatch.setattr(render, 'run_ff', failing_hardware)
    try:
        result = render.render(p, jobs.Job(p.id, 'review'), preview=True)
    except ValueError:
        print({'encoder_attempts': attempts})
        raise
    assert result['encoder'] == 'libx264'
    assert result['encoder_fallback'] and not result['hardware_encoder']
    assert len(failed)==1 and attempts.count((failed[0],'libx264'))==1
    assert render.video_encoder()[0]=='libx264'
    if stage=='clip':
        warm=render.render(p,jobs.Job(p.id,'review-warm'),preview=True)
        assert warm['clip_cache_hits']==2


def test_valid_adjacent_locked_crossfade_can_be_replanned():
    a = Asset(name='Source', filename='source.mp4', media='video', duration=10,
              fps=30, scenes=[Scene(start=0, end=10)])
    p = Project(target_duration=5, assets=[a], clips=[
        Clip(asset_id=a.id, scene_id=a.scenes[0].id, duration=2.75, locked=True),
        Clip(asset_id=a.id, scene_id=a.scenes[0].id, source_start=3,
             duration=2.75, locked=True, transition='crossfade', transition_duration=.5),
    ])
    assert preflight(p)['ok']
    original = clip_starts(p.clips)
    print({'preflight_ok': True, 'valid_locked_starts': original})
    result = planner.plan(p, False, jobs.Job(p.id, 'review-plan'))
    assert clip_starts(result.clips) == original


def test_ai_preview_matches_committed_voice_and_cue_state():
    p = make_media_project(include_music=False)
    p.script = 'Original script'
    p = store.save(p)
    request = {'revision': p.revision, 'script': 'Changed script'}
    client = TestClient(app)
    response = client.post(f'/api/projects/{p.id}/assistant-preview', json=request)
    assert response.status_code == 200 and response.json()['can_apply']
    preview = response.json()
    applied = client.post(f'/api/projects/{p.id}/apply-ai', json={
        **request, 'preview_token': preview['preview_token']})
    assert applied.status_code == 200
    print({'preview_has_voice': bool(preview['project']['voice_id']),
           'committed_has_voice': bool(applied.json()['voice_id'])})
    assert preview['project']['voice_id'] == applied.json()['voice_id']
    assert preview['project']['cues_stale'] == applied.json()['cues_stale']
    assert preview['project']['warnings']==applied.json()['warnings']
    undone=client.post(f'/api/projects/{p.id}/undo')
    assert undone.status_code==200
    assert undone.json()['voice_id']==p.voice_id and undone.json()['script']==p.script


def test_label_cache_does_not_cross_compatible_endpoints(monkeypatch):
    profiles = [dict(id=name, name=name, provider='compatible', model='same-model',
                     base_url=f'http://127.0.0.1:{port}/v1', api_key='', enabled=True)
                for name, port in [('one', 19001), ('two', 19002)]]
    store.save_settings({'ai_profiles': profiles, 'active_ai_profile_id': 'one', 'ai_auto_fallback': False})
    p = Project()
    thumb = store.project_dir(p.id) / 'thumbs' / 'scene.jpg'
    thumb.parent.mkdir(parents=True)
    Image.new('RGB', (20, 20), 'red').save(thumb)
    scene = Scene(start=0, end=2, thumbnail=thumb.name)
    p.assets = [Asset(name='Source', filename='source.mp4', media='video', duration=2, scenes=[scene])]
    calls = []

    def mocked_ai(*args, **kwargs):
        current = providers.ai_candidates(store.settings())[0]
        calls.append(current.id)
        return ({'scenes': [{'index': 0, 'tags': current.id}]},
                {'provider': current.provider, 'model': current.model, 'endpoint_id': providers._endpoint_id(current)})

    monkeypatch.setattr(providers, 'ai_json', mocked_ai)
    providers.label_scenes(p, jobs.Job(p.id, 'review-label'))
    store.save_settings({'active_ai_profile_id': 'two'})
    scene.ai_labeled = False
    providers.label_scenes(p, jobs.Job(p.id, 'review-label'))
    print({'provider_calls': calls, 'tags_after_switch': scene.tags})
    assert calls == ['one', 'two'] and scene.tags == 'two'


def test_crossfade_caption_overrides_do_not_stack_at_same_clock(tmp_path):
    p = Project(clips=[Clip(asset_id='a', duration=2, caption='First'),
                       Clip(asset_id='b', duration=2, caption='Second',
                            transition='crossfade', transition_duration=.5)])
    render.subtitles(p, 720, 1280, tmp_path)
    srt = (tmp_path / 'captions.srt').read_text(encoding='utf-8-sig')
    print(srt)
    assert '00:00:00,000 --> 00:00:01,500\nFirst' in srt


def test_font_catalog_uses_real_family_names():
    names = font_manager.catalog()
    path, actual, found = font_manager.resolve('arialbd')
    if not found:
        pytest.skip('Windows Arial filename alias not installed')
    assert 'arialbd' not in names
    real_family = ImageFont.truetype(path, 24).getname()[0]
    print({'catalog_family': 'arialbd', 'resolved_family': actual, 'font_file_family': real_family,
           'preflight_found': found})
    assert actual.casefold() == real_family.casefold()
    assert Layer(font_family='arialbd').font_family==real_family
    assert CaptionStyle(font_family='arialbd').font_family==real_family


@pytest.mark.parametrize('failure',['input','cancel','cpu_failure'])
def test_encoder_fallback_is_bounded_and_respects_input_errors_and_cancel(tmp_path,monkeypatch,failure):
    encoder=['h264_qsv',True,[]]
    job=jobs.Job('project','encode')
    calls=[]
    def fail(args,**kwargs):
        calls.append(args[args.index('-c:v')+1])
        if failure=='cancel':job.cancelled.set()
        if failure=='input':raise ValueError('Invalid data found when processing input')
        raise ValueError('Hardware device unavailable')
    monkeypatch.setattr(render,'run_ff',fail)
    monkeypatch.setattr(render,'_VIDEO_ENCODER',tuple(encoder))
    expected=jobs.Cancelled if failure=='cancel' else ValueError
    with pytest.raises(expected):render._run_video_encode([],tmp_path/'out.mp4',True,encoder,tmp_path,job)
    assert calls==(['h264_qsv','libx264'] if failure=='cpu_failure' else ['h264_qsv'])


def test_first_locked_crossfade_and_partial_locked_chain_keep_positions():
    a=Asset(name='Source',filename='source.mp4',media='video',duration=20,
            scenes=[Scene(start=0,end=20)])
    clips=[Clip(asset_id=a.id,duration=2,transition='crossfade',transition_duration=.5,locked=True),
           Clip(asset_id=a.id,duration=2,transition='crossfade',transition_duration=.5),
           Clip(asset_id=a.id,duration=2,transition='crossfade',transition_duration=.5,locked=True)]
    p=Project(target_duration=7,assets=[a],clips=clips)
    original={clip.id:(clip_starts(clips)[index],clip.model_dump())
              for index,clip in enumerate(clips) if clip.locked}
    planned=planner.plan(p,False,jobs.Job(p.id,'locked-chain'))
    for index,clip in enumerate(planned.clips):
        if clip.id in original:
            assert clip_starts(planned.clips)[index]==pytest.approx(original[clip.id][0])
            assert clip.model_dump()==original[clip.id][1]


def test_ai_preflight_uses_normalized_candidate_and_token_selection_is_bound():
    p=make_media_project(include_music=False)
    p.script='Original';p.clips[0].duration=3;p=store.save(p)
    client=TestClient(app)
    request={'revision':p.revision,'script':'New'}
    preview=client.post(f'/api/projects/{p.id}/assistant-preview',json=request)
    assert preview.status_code==200 and preview.json()['can_apply']
    assert not preview.json()['project']['voice_id']
    assert not any(issue['code']=='voice.longer_than_timeline' for issue in preview.json()['preflight']['issues'])
    # Checking a proposal never changes the saved project.
    assert store.read(p.id).voice_id==p.voice_id
    invalid=client.post(f'/api/projects/{p.id}/apply-ai',json={**request,'music_volume':.3,
                        'preview_token':preview.json()['preview_token']})
    assert invalid.status_code==409 and store.read(p.id).script=='Original'


def test_caption_handoff_to_blank_incoming_restores_voice_cue(tmp_path):
    p=Project(clips=[Clip(asset_id='a',duration=2,caption='Override'),
                     Clip(asset_id='b',duration=2,transition='crossfade',transition_duration=.5)],
              cues=[Cue(start=0,end=3.5,text='Voice')])
    render.subtitles(p,720,1280,tmp_path)
    srt=(tmp_path/'captions.srt').read_text(encoding='utf-8-sig')
    assert '00:00:00,000 --> 00:00:01,500\nOverride' in srt
    assert '00:00:01,500 --> 00:00:03,500\nVoice' in srt


def test_provider_cancel_stops_retry_and_does_not_record_secrets(monkeypatch):
    import httpx,json
    profiles=[dict(id=name,name=name,provider='compatible',model='same-model',
                   base_url=f'http://127.0.0.1:{port}/v1',api_key='review-secret',enabled=True)
              for name,port in [('one',19001),('two',19002)]]
    store.save_settings({'ai_profiles':profiles,'active_ai_profile_id':'one','ai_auto_fallback':True})
    job=jobs.Job(Project().id,'cancel-provider');calls=[]
    def respond(request):
        calls.append(str(request.url));job.cancelled.set()
        return httpx.Response(503,json={'error':'review-secret'})
    real_client=httpx.Client
    monkeypatch.setattr(providers.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    with pytest.raises(jobs.Cancelled):providers.ai_json('Fixture',job=job)
    assert len(calls)==1
    assert 'review-secret' not in json.dumps(job.dump())


def test_real_provider_route_cache_is_written_for_fallback_endpoint(monkeypatch):
    import httpx
    profiles=[dict(id=name,name=name,provider='compatible',model='same-model',
                   base_url=f'http://127.0.0.1:{port}/v1',api_key='',enabled=True)
              for name,port in [('one',19001),('two',19002)]]
    store.save_settings({'ai_profiles':profiles,'active_ai_profile_id':'one','ai_auto_fallback':True})
    p=Project();thumb=store.project_dir(p.id)/'thumbs'/'scene.jpg';thumb.parent.mkdir(parents=True)
    Image.new('RGB',(20,20),'red').save(thumb)
    scene=Scene(start=0,end=2,thumbnail=thumb.name)
    p.assets=[Asset(name='Source',filename='source.mp4',media='video',duration=2,scenes=[scene])]
    calls=[]
    def respond(request):
        assert 'authorization' not in request.headers
        calls.append(request.url.port)
        if request.url.port==19001:return httpx.Response(400,json={'error':'invalid model'})
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"scenes":[{"index":0,"tags":"fallback"}]}'}}]})
    real_client=httpx.Client
    monkeypatch.setattr(providers.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    providers.label_scenes(p,jobs.Job(p.id,'fallback-label'))
    assert calls==[19001,19002] and scene.tags=='fallback'
    store.save_settings({'active_ai_profile_id':'two','ai_auto_fallback':False})
    scene.ai_labeled=False
    providers.label_scenes(p,jobs.Job(p.id,'cached-fallback'))
    assert calls==[19001,19002] and scene.tags=='fallback'


def test_real_crossfade_pixels_vietnamese_font_and_caption_handoff():
    p=Project(name='Kiểm tra dấu tiếng Việt',aspect='16:9',resolution='720')
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    for color in ('red','blue'):
        path=root/f'{color}.png';Image.new('RGB',(320,180),color).save(path)
        p.assets.append(Asset(name=color,filename=path.name,**media.probe(path)))
    p.clips=[Clip(asset_id=p.assets[0].id,duration=2,caption='Cảnh đầu tiên'),
             Clip(asset_id=p.assets[1].id,duration=2,caption='Đúng mốc chuyển cảnh',
                  transition='crossfade',transition_duration=.5)]
    p.template.caption=CaptionStyle(font_family=font_manager.resolve('Arial')[1])
    p.template.background='#00ff00'
    p.template.viewport.x=.1;p.template.viewport.y=.1;p.template.viewport.w=.8;p.template.viewport.h=.8
    p=store.save(p)
    result=render.render(p,jobs.Job(p.id,'pixel-font'),preview=False)
    output=store.project_dir(p.id)/'exports'/result['id']/result['filename']
    middle=None;caption_frame=None
    with av.open(str(output)) as container:
        for frame in container.decode(video=0):
            time=float(frame.pts*frame.time_base)
            if middle is None and time>=1.74:middle=frame.to_ndarray(format='rgb24')
            if time>=2.5:caption_frame=frame.to_ndarray(format='rgb24');break
    pixel=middle[350,640].astype(float)
    assert abs(pixel[0]-127)<22 and pixel[1]<20 and abs(pixel[2]-127)<22
    assert middle[20,20,1]>230 and middle[20,20,0]<20
    assert np.count_nonzero(np.all(caption_frame[500:650,:,:]>220,axis=2))>30
    ass=(output.parent/'captions.ass').read_text(encoding='utf-8-sig')
    assert 'Đúng mốc chuyển cảnh' in ass
    assert f'Style: Default,{p.template.caption.font_family},' in ass
    srt=(output.parent/'captions.srt').read_text(encoding='utf-8-sig')
    assert '00:00:00,000 --> 00:00:01,500\nCảnh đầu tiên' in srt
    assert '00:00:01,500 --> 00:00:03,500\nĐúng mốc chuyển cảnh' in srt
