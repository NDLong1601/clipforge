import wave

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import jobs, media, render, sound_effects, store, template_packages
from backend.main import app
from backend.models import Asset, Clip, Cue, Layer, Project, Scene, SoundEffect, Template
from backend.template_content import bind_placeholders, layer_text, product_values
from backend.timeline_math import clip_starts, timeline_duration


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(render, 'video_encoder', lambda: ('libx264', False, []))


def test_frame_boundaries_do_not_accumulate_rounding():
    p = Project(clips=[Clip(asset_id='a', duration=.237) for _ in range(120)])
    clock = render._frame_project(p)
    assert abs(timeline_duration(clock.clips) - timeline_duration(p.clips)) <= 1/60
    for before, after in zip(clip_starts(p.clips), clip_starts(clock.clips)):
        assert abs(before - after) <= 1/60 + 1e-9
    assert p.clips[0].duration == .237


@pytest.mark.parametrize('transition', ['cut', 'crossfade'])
def test_real_fractional_export_matches_preview_clock_and_audio(isolated, monkeypatch, transition):
    monkeypatch.setattr(render, 'dimensions', lambda *args: (320, 180))
    p = Project(aspect='16:9', auto_sound_effects=False)
    root = store.project_dir(p.id) / 'assets'; root.mkdir(parents=True)
    for color in ('red', 'blue'):
        path = root / (color + '.png'); Image.new('RGB', (160, 90), color).save(path)
        p.assets.append(Asset(name=color, filename=path.name, **media.probe(path)))
    p.clips = [
        Clip(asset_id=p.assets[index % 2].id, duration=.437, title=str(index),
             transition=transition if index else 'cut', transition_duration=.1,
             layers=[Layer(text='{title}', x=.05, y=.1, w=.2, h=.2)]) for index in range(12)]
    duration = timeline_duration(p.clips)
    p.cues = [Cue(start=0, end=duration, text='Kiểm tra đồng bộ hình và âm thanh.')]
    pulse = np.zeros(round(duration*48000), dtype=np.float32)
    pulse[48000:48480] = .3*np.sin(2*np.pi*700*np.arange(480)/48000)
    path = root / 'voice.wav'; sound_effects._write_wav(path, pulse)
    voice = Asset(name='voice', filename=path.name, role='voice', **media.probe(path))
    p.assets.append(voice); p.voice_id=voice.id
    starts=[]
    for preview in (True, False):
        result = render.render(p, jobs.Job(p.id, 'render'), preview=preview)
        output = store.project_dir(p.id) / 'exports' / result['id'] / result['filename']
        with av.open(str(output)) as container:
            times=[float(frame.pts*frame.time_base) for frame in container.decode(video=0)]
        assert len(times)==round(duration*30)
        assert np.diff(times)==pytest.approx(np.full(len(times)-1, 1/30), abs=1e-7)
        with av.open(str(output)) as container:
            resampler=av.AudioResampler(format='flt', layout='mono', rate=48000)
            values=[converted.to_ndarray().ravel() for frame in container.decode(audio=0) for converted in resampler.resample(frame)]
        signal=np.concatenate(values)
        start=np.flatnonzero(np.abs(signal)>.03)[0]/48000
        assert abs(start-1)<.015
        starts.append(start)
        assert result['clock_verified'] and result['render_version']==render.RENDER_VERSION
    assert starts[0]==pytest.approx(starts[1], abs=1/48000)


def test_original_library_and_precise_effect_placement(isolated):
    assert len(sound_effects.catalog())==10
    for item in sound_effects.catalog():
        path=sound_effects.effect_path(item['id'])
        with wave.open(str(path)) as wav:
            assert wav.getframerate()==48000
            assert wav.getnframes()==round(item['duration']*48000)
    p=Project(auto_sound_effects=False, sound_effect_volume=.5,
              clips=[Clip(asset_id='a', duration=5)],
              sound_effects=[SoundEffect(effect='ding', time=2, volume=.8)])
    with wave.open(str(sound_effects.track_path(p))) as wav:
        data=np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    assert np.count_nonzero(data[:96000])==0
    assert np.max(np.abs(data[96000:]))>1000


def test_automatic_effects_prioritize_emphasis_keep_manual_and_disable(isolated):
    p=Project(clips=[Clip(asset_id='a', duration=1) for _ in range(15)],
              cues=[Cue(start=4, end=5, text='Đặc biệt, thiết kế nhỏ gọn.'),
                    Cue(start=10, end=12, text='Khám phá ngay hôm nay.')],
              sound_effects=[SoundEffect(effect='camera', time=.3)])
    sound_effects.refresh(p)
    assert any(e.effect=='ding' and e.time==4 for e in p.sound_effects)
    assert any(e.effect=='success' and e.time==10 for e in p.sound_effects)
    assert p.sound_effects[0].origin=='manual'
    snapshot=p.model_dump()
    sound_effects.refresh(p)
    assert p.model_dump()==snapshot
    p.auto_sound_effects=False; sound_effects.refresh(p)
    assert len(p.sound_effects)==1 and p.sound_effects[0].effect=='camera'


def test_legacy_template_product_fields_follow_new_source_and_script(isolated):
    template=Template(layers=[Layer(text='TÊN SẢN PHẨM'), Layer(text='MÔ TẢ SẢN PHẨM'), Layer(text='KHÁM PHÁ SẢN PHẨM')])
    bind_placeholders(template)
    p=Project(template=template, script='Thiết kế nhỏ gọn, dễ mang theo.',
              assets=[Asset(name='Bình_nước_mới.mp4', filename='new.mp4', media='video')])
    clip=Clip(asset_id=p.assets[0].id, title='Cảnh thứ nhất')
    assert layer_text(p, template.layers[0], clip)=='Bình nước mới'
    assert layer_text(p, template.layers[1], clip)==p.script
    p.product_name='Tên đã chỉnh';p.product_description='Mô tả đã chỉnh'
    assert layer_text(p, template.layers[0], clip)=='Tên đã chỉnh'
    p.assets.append(Asset(name='Nguồn_thứ_hai.mp4', filename='second.mp4', media='video'))
    assert product_values(p)['product_name']=='Tên đã chỉnh'
    p.product_name=''
    assert product_values(p)['product_name']=='Nguồn thứ hai'


def test_template_package_api_binding_and_sfx_routes(isolated):
    p=store.save(Project(template=Template(layers=[Layer(text='TÊN SẢN PHẨM')])))
    package=template_packages.save_package(p,p.template)
    with TestClient(app) as client:
        listing=client.get('/api/template-packages').json()
        assert listing[0]['template']['layers'][0]['content']=='product_name'
        applied=client.post(f'/api/projects/{p.id}/templates/{package["id"]}/apply',json={}).json()
        assert applied['template']['layers'][0]['content']=='product_name'
        assert len(client.get('/api/sound-effects').json())==10
        assert client.get('/api/sound-effects/pop.wav').content[:4]==b'RIFF'
        assert client.get('/api/sound-effects/not-valid.wav').status_code==400


def test_word_cues_insert_effect_at_emphasized_phrase_not_sentence_start(isolated):
    p=Project(clips=[Clip(asset_id='a', duration=8)], cues=[
        Cue(start=0,end=.5,text='Thiết'),Cue(start=.5,end=1,text='kế'),
        Cue(start=1,end=1.5,text='này'),Cue(start=1.5,end=2,text='đặc'),
        Cue(start=2,end=2.5,text='biệt.')])
    events=sound_effects.effective_events(p)
    assert [(e.effect,e.time) for e in events]==[('ding',1.5)]


def test_ai_product_name_is_bound_to_new_source_and_respects_manual_fields(isolated,monkeypatch):
    from backend import providers
    from backend.template_content import infer_product
    p=Project(mode='template', template=Template(layers=[Layer(content='product_name')]),
              assets=[Asset(name='Video 1.mp4',filename='first.mp4',media='video')])
    monkeypatch.setattr(providers,'ai_json',lambda *args,**kwargs:{'name':'Bình giữ nhiệt','description':'Lọ màu xanh.'})
    infer_product(p,jobs.Job(p.id,'plan'))
    assert product_values(p)['product_name']=='Bình giữ nhiệt'
    p.assets.append(Asset(name='Nguồn mới.mp4',filename='second.mp4',media='video'))
    assert product_values(p)['product_name']=='Nguồn mới'
    p.product_name='Do người dùng đặt'
    infer_product(p,jobs.Job(p.id,'plan'))
    assert product_values(p)['product_name']=='Do người dùng đặt'


def test_vfr_high_resolution_source_with_proxy_speed_and_source_audio(isolated,monkeypatch):
    monkeypatch.setattr(render, 'dimensions', lambda *args: (320,180))
    p=Project(aspect='16:9',source_volume=.4,auto_sound_effects=False)
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    source=root/'vfr.mp4'
    media.run_ff(['-f','lavfi','-i','testsrc2=size=1920x1080:rate=24:duration=1.5',
                  '-f','lavfi','-i','sine=frequency=600:sample_rate=48000:duration=2',
                  '-vf','setpts=if(lt(N\\,12)\\,N/(24*TB)\\,(N-12)/(18*TB)+0.5/TB)',
                  '-fps_mode','vfr','-c:v','libx264','-preset','ultrafast','-crf','28',
                  '-c:a','aac',source])
    asset=Asset(name='VFR',filename=source.name,**media.probe(source));p.assets=[asset]
    p.clips=[Clip(asset_id=asset.id,source_start=.13,duration=.937,speed=1.12,
                  layers=[Layer(text='Chữ tiếng Việt',animation='slide')]),
             Clip(asset_id=asset.id,source_start=.9,duration=.763,speed=.8,
                  transition='crossfade',transition_duration=.133)]
    for preview in (True,False):
        result=render.render(p,jobs.Job(p.id,'vfr-check'),preview=preview)
        assert result['clock_verified']
        assert result['proxies_used']==len(p.clips)*int(preview)
        output=store.project_dir(p.id)/'exports'/result['id']/result['filename']
        with av.open(str(output)) as container:
            assert container.streams.audio[0].sample_rate==48000


def test_long_export_keeps_clock_when_source_color_metadata_changes(isolated,monkeypatch):
    monkeypatch.setattr(render,'dimensions',lambda *args:(160,90))
    p=Project(aspect='16:9',auto_sound_effects=False,
              sound_effects=[SoundEffect(effect='ding',time=20.2)])
    root=store.project_dir(p.id)/'assets';root.mkdir(parents=True)
    for index,color in enumerate(('red','blue')):
        source=root/f'color{index}.mp4'
        flags=['-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709'] if index==0 else []
        media.run_ff(['-f','lavfi','-i',f'color=c={color}:s=160x90:r=30:d=2',
                      '-c:v','libx264','-preset','ultrafast',*flags,source])
        p.assets.append(Asset(name=color,filename=source.name,**media.probe(source)))
    p.clips=[Clip(asset_id=p.assets[index%2].id,duration=1.375) for index in range(16)]
    result=render.render(p,jobs.Job(p.id,'long-color'),preview=False)
    assert result['duration']==22 and result['clock_verified']
    output=store.project_dir(p.id)/'exports'/result['id']/result['filename']
    with av.open(str(output)) as container:
        frames=list(container.decode(video=0))
    assert len(frames)==660
    with av.open(str(output)) as container:
        pts=[packet.pts for packet in container.demux(audio=0) if packet.size]
    assert all(right-left==1024 for left,right in zip(pts,pts[1:]))
