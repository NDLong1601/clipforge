import base64, json, re, os, subprocess, html
from pathlib import Path
import httpx
from .models import Cue, Asset, Template, uid
from .store import settings, project_dir, asset_path
from .media import probe, run_ff, extract_audio, register

def checked(response):
    if response.is_error:
        # Don't echo provider response bodies: they may include credentials or request data.
        raise ValueError(f'Dịch vụ AI trả lỗi HTTP {response.status_code}. Kiểm tra API key, model, hạn mức và địa chỉ API.')
    return response

def ai_candidates(s, profile_id=None, allow_fallback=None):
    profiles=[p for p in s.ai_profiles if p.enabled]
    if profile_id:
        selected=next((p for p in profiles if p.id==profile_id),None)
        if selected is None:
            raise ValueError('Cấu hình API đã chọn không tồn tại hoặc đang tắt')
    else:
        selected=next((p for p in profiles if p.id==s.active_ai_profile_id),None)
        if selected is None and profiles:
            selected=profiles[0]
    if selected is None:
        raise ValueError('Chưa có cấu hình AI đang bật. Mở Cài đặt API để thêm khóa và model.')
    fallback=s.ai_auto_fallback if allow_fallback is None else allow_fallback
    return [selected]+[p for p in profiles if p.id!=selected.id] if fallback else [selected]

def ai_endpoint(profile):
    if profile.provider=='openai':
        return 'https://api.openai.com/v1'
    if profile.provider=='gemini':
        return 'https://generativelanguage.googleapis.com/v1beta/openai'
    return profile.base_url.rstrip('/')

def audio_connection(s):
    candidates=[p for p in s.ai_profiles
                if p.enabled and p.provider in ('openai','compatible') and p.api_key]
    chosen=next((p for p in candidates if p.id==s.active_ai_profile_id),None)
    if chosen is None and candidates:
        chosen=candidates[0]
    if chosen:
        return ai_endpoint(chosen),chosen.api_key
    if s.ai_key:
        return s.ai_base_url.rstrip('/'),s.ai_key
    raise ValueError('Cần khóa OpenAI hoặc API tương thích hỗ trợ Speech. Gemini trong danh sách AI chỉ dùng cho biên tập.')

def ai_json(prompt, images=None, system='Bạn là trợ lý biên tập video. Trả về JSON hợp lệ, không markdown.',
            profile_id=None, allow_fallback=None, return_route=False):
    s=settings()
    content=[{'type':'text','text':prompt}]
    for path in images or []:
        content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode(),'detail':'low'}})
    failures=[]
    for profile in ai_candidates(s,profile_id,allow_fallback):
        url=ai_endpoint(profile)
        if not profile.api_key and not url.startswith(('http://localhost','http://127.0.0.1')):
            failures.append(f'{profile.name} ({profile.model}): thiếu API key')
            continue
        body={'model':profile.model,
              'messages':[{'role':'system','content':system},{'role':'user','content':content}]}
        if profile.provider=='openai':
            body['response_format']={'type':'json_object'}
        try:
            with httpx.Client(timeout=180) as client:
                response=client.post(url+'/chat/completions',
                    headers={'Authorization':'Bearer '+profile.api_key},json=body)
            if response.is_error:
                failures.append(f'{profile.name} ({profile.model}): HTTP {response.status_code}')
                continue
            answer=response.json()['choices'][0]['message']['content']
            if not isinstance(answer,str):
                raise ValueError('phản hồi không có nội dung')
            answer=answer.strip()
            if answer.startswith('```'):
                answer=re.sub(r'^\`\`\`(?:json)?\s*|\s*\`\`\`$', '',answer,flags=re.I).strip()
            result=json.loads(answer)
            if not isinstance(result,dict):
                raise ValueError('phản hồi không phải JSON object')
            route={'id':profile.id,'name':profile.name,'provider':profile.provider,'model':profile.model,
                   'fallback_used':profile.id!=ai_candidates(s,profile_id,False)[0].id}
            return (result,route) if return_route else result
        except httpx.TimeoutException:
            reason='hết thời gian chờ'
        except httpx.RequestError:
            reason='lỗi mạng'
        except (ValueError,KeyError,TypeError,IndexError):
            reason='phản hồi JSON không hợp lệ'
        failures.append(f'{profile.name} ({profile.model}): {reason}')
    raise ValueError('Không kết nối được AI qua các cấu hình: '+'; '.join(failures))

def label_scenes(p,job):
    items=[(a,sc) for a in p.assets if a.role=='source' and not a.deleted for sc in a.scenes if not sc.ai_labeled]
    for start in range(0,len(items),6):
        job.update(35+50*start/max(1,len(items)),'AI đang mô tả các cảnh')
        group=items[start:start+6]
        result=ai_json('Mỗi ảnh tương ứng một cảnh theo thứ tự. Mô tả tiếng Việt ngắn, đối tượng, hành động, bối cảnh, chữ có sẵn. Không suy đoán danh tính. JSON {"scenes":[{"index":0,"tags":"..."}]}.',
            [project_dir(p.id)/'thumbs'/sc.thumbnail for a,sc in group])
        for entry in result.get('scenes',[]):
            index=entry.get('index')
            if isinstance(index,int) and 0<=index<len(group):
                group[index][1].tags=str(entry.get('tags',''))[:1500]
                group[index][1].ai_labeled=True
    return p

def infer_template(p,asset,job):
    from .template_utils import compressed_slot_durations
    scenes=asset.scenes
    picks=scenes[::max(1,len(scenes)//6)][:6]
    prompt='''Phân tích bố cục video mẫu từ các ảnh theo thời gian. Tạo template dựng lại với nội dung mới, giữ hình khối, vị trí khung, text, màu và biểu tượng đơn giản. Không giả vờ tách được logo/font gốc. Trả JSON theo schema:
{"name":"Tên mẫu","background":"#111111","viewport":{"x":0,"y":0,"w":1,"h":1},"transition":"cut","layers":[{"kind":"text|rect|circle","x":0.07,"y":0.08,"w":0.86,"h":0.12,"text":"{title}","size":54,"color":"#ffffff","background":"#000000","opacity":1,"animation":"none"}],"caption":{"enabled":true,"font_size":52,"color":"#ffffff","highlight":"#b5f36d","bottom":0.18,"words_per_line":6,"karaoke":false},"notes":"Các chi tiết cần người dùng duyệt"}.
Tọa độ 0..1; x+w,y+h <=1. size tính theo chiều rộng 1080. {title} sẽ thay bằng tiêu đề từng đoạn; {project} là tên dự án. Không giữ nguyên câu chữ nội dung mẫu. Nếu mẫu có nhiều bố cục, thêm slot_layers là các mảng layers theo thứ tự ảnh. Chỉ dùng trường schema trên.'''
    result=ai_json(prompt,[project_dir(p.id)/'thumbs'/x.thumbnail for x in picks])
    result['slot_durations']=compressed_slot_durations(asset)
    if len(scenes)>100:
        note='Đã gộp nhịp liên tiếp để vừa giới hạn 100 ô; toàn bộ cảnh gốc và thời lượng video mẫu vẫn được giữ.'
        result['notes']=(str(result.get('notes','')).strip()+' '+note).strip()
    try: return Template.model_validate(result)
    except Exception as e: raise ValueError('AI trả bố cục không hợp lệ. Hãy thử lại hoặc sửa mẫu thủ công.') from e

def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n+',text.strip()) if s.strip()]

def speak_sentence(text,dest,s,job):
    job.check()
    if s.tts_provider=='openai':
        base_url,key=audio_connection(s)
        with httpx.Client(timeout=120) as client:
            r=checked(client.post(base_url+'/audio/speech',
                headers={'Authorization':'Bearer '+key},json={'model':s.tts_model,
                'voice':s.tts_voice,'input':text,'response_format':'wav','speed':s.tts_speed}))
            dest.write_bytes(r.content)
    elif s.tts_provider=='azure':
        if not s.azure_key: raise ValueError('Cần Azure Speech key để tạo giọng tiếng Việt')
        if not re.fullmatch('[a-z0-9-]+',s.azure_region): raise ValueError('Azure region không hợp lệ')
        ssml=f'<speak version="1.0" xml:lang="vi-VN"><voice name="{html.escape(s.azure_voice,quote=True)}"><prosody rate="{s.tts_speed:.2f}">{html.escape(text)}</prosody></voice></speak>'
        with httpx.Client(timeout=120) as client:
            r=checked(client.post(f'https://{s.azure_region}.tts.speech.microsoft.com/cognitiveservices/v1',
                headers={'Ocp-Apim-Subscription-Key':s.azure_key,'Content-Type':'application/ssml+xml',
                'X-Microsoft-OutputFormat':'riff-24khz-16bit-mono-pcm'},content=ssml.encode()))
            dest.write_bytes(r.content)
    else:
        if os.name!='nt': raise ValueError('Giọng Windows chỉ dùng trên Windows')
        config=dest.with_suffix('.json')
        config.write_text(json.dumps({'text':text,'path':str(dest),'voice':s.windows_voice,'rate':round((s.tts_speed-1)*5)},ensure_ascii=False),encoding='utf-8')
        ps=dest.with_suffix('.ps1')
        ps.write_text('''param([string]$ConfigPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($cfg.voice) { $speech.SelectVoice($cfg.voice) }
$speech.Rate = $cfg.rate
$speech.SetOutputToWaveFile($cfg.path)
$speech.Speak($cfg.text)
$speech.Dispose()
''',encoding='utf-8-sig')
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ps),str(config)],capture_output=True,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
        config.unlink(missing_ok=True); ps.unlink(missing_ok=True)
        if result.returncode: raise ValueError('Không tạo được giọng Windows. Kiểm tra tên giọng đã cài.')
    job.check()

def synthesize(p,job):
    parts=sentences(p.script)
    if not parts: raise ValueError('Nhập kịch bản trước khi tạo giọng')
    if len(p.script)>6000: raise ValueError('Kịch bản tạo giọng tối đa 6.000 ký tự')
    s=settings(); d=project_dir(p.id)/'cache'/job.id; d.mkdir(parents=True,exist_ok=True)
    cues=[]; total=0
    for i,text in enumerate(parts):
        job.update(5+75*i/len(parts),f'Tạo giọng câu {i+1}/{len(parts)}')
        wav=d/f'speech_{i:03d}.wav'
        speak_sentence(text,wav,s,job)
        normalized=d/f'part_{i:03d}.wav'
        run_ff(['-i',wav,'-ar','48000','-ac','2',normalized],job=job)
        duration=probe(normalized)['duration']
        cues.append(Cue(start=total,end=total+duration,text=text)); total+=duration
    if total>180: raise ValueError('Giọng dài quá 180 giây. Hãy rút gọn kịch bản.')
    (d/'list.txt').write_text('\n'.join(f"file 'part_{i:03d}.wav'" for i in range(len(parts))),encoding='utf-8')
    out=project_dir(p.id)/'assets'/f'{uid()}.wav'; out.parent.mkdir(parents=True,exist_ok=True)
    run_ff(['-f','concat','-safe','0','-i','list.txt','-c:a','pcm_s16le',out],cwd=d,job=job)
    a=register(p.id,out,'Giọng đọc AI.wav','voice',job)
    p.assets.append(a); p.voice_id=a.id; p.cues=cues; p.cue_timing='sentence-exact'
    p.cues_edited=False; p.cues_stale=False
    if total>p.target_duration:
        p.target_duration=min(180,round(total,2))
        p.warnings=['Giọng dài hơn thời lượng đã chọn; timeline được kéo dài để giữ trọn lời đọc.']
    return p

def transcribe(p,asset,job):
    s=settings(); d=project_dir(p.id)/'cache'/job.id; d.mkdir(parents=True,exist_ok=True)
    wav=d/'audio.wav'; extract_audio(asset_path(p.id,asset),wav,job)
    job.update(25,'Nhận dạng lời đọc và mốc thời gian')
    if s.transcription_provider=='local':
        try: from faster_whisper import WhisperModel
        except ImportError as e: raise ValueError('Chưa cài faster-whisper. Chạy: pip install faster-whisper. Model sẽ được tải ở lần dùng đầu.') from e
        model=WhisperModel(s.local_whisper_model,device='cpu',compute_type='int8')
        segments,_=model.transcribe(str(wav),language=s.language or None,word_timestamps=True)
        cues=[Cue(start=max(0,w.start),end=max(w.start+.02,w.end),text=w.word.strip()) for seg in segments for w in seg.words if w.word.strip()]
    else:
        base_url,key=audio_connection(s)
        if wav.stat().st_size>24*1024*1024: raise ValueError('Âm thanh quá lớn. Chỉ nhận dạng voice của video đầu ra, tối đa khoảng 12 phút.')
        with httpx.Client(timeout=240) as client, wav.open('rb') as f:
            r=checked(client.post(base_url+'/audio/transcriptions',
                headers={'Authorization':'Bearer '+key},files={'file':('audio.wav',f,'audio/wav')},
                data={'model':s.transcription_model,'response_format':'verbose_json','timestamp_granularities[]':'word','language':s.language}))
        data=r.json()
        words=data.get('words') or data.get('segments') or []
        cues=[Cue(start=max(0,w['start']),end=max(w['start']+.02,w['end']),text=w.get('word',w.get('text','')).strip()) for w in words]
    if not cues: raise ValueError('Không nhận được mốc thời gian. Chọn whisper-1 hoặc bộ nhận dạng cục bộ.')
    job.check()
    p.cues=cues; p.cue_timing='transcribed'; p.voice_id=asset.id
    p.cues_edited=False; p.cues_stale=False
    p.script=' '.join(c.text for c in cues)
    if asset.duration>p.target_duration: p.target_duration=min(180,asset.duration)
    return p

def assistant(p,prompt):
    context={'script':p.script,'music_volume':p.music_volume,'clips':[c.model_dump() for c in p.clips],
        'sources':[{'id':a.id,'name':a.name,'tags':a.tags} for a in p.assets if a.role=='source']}
    return ai_json('Dựa vào yêu cầu, đề xuất chỉnh sửa dự án. Không thực thi mã. JSON {"message":"giải thích tiếng Việt","script":null,"music_volume":null,"clip_changes":[{"id":"clip id","title":"...","caption":"...","asset_id":"source id","source_start":0,"duration":2.5}]}. Chỉ đưa trường cần sửa. Không bịa ID. Giữ nguyên lời đọc khi chỉ thay cảnh. Yêu cầu: '+prompt+'\nDự án: '+json.dumps(context,ensure_ascii=False))
