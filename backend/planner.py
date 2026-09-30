import math, re, unicodedata, json
from collections import Counter
from .models import Template, Layer, Viewport, Clip, Cue
from .providers import sentences, ai_json
from .store import get_asset

def presets():
    return [Template(id='clean',name='Toàn khung',layers=[Layer(kind='text',text='{project}',x=.07,y=.06,w=.86,h=.07,size=30,color='#b5f36d')]),
        Template(id='editorial',name='Khung biên tập',background='#f0eadf',viewport=Viewport(x=.055,y=.2,w=.89,h=.57),
            layers=[Layer(kind='text',text='{title}',x=.06,y=.055,w=.88,h=.13,size=60,color='#20221f'),
                Layer(kind='rect',x=.06,y=.79,w=.18,h=.009,color='#2b703e',text=''),
                Layer(kind='text',text='{project}',x=.06,y=.81,w=.88,h=.06,size=27,color='#2b703e')],
            caption={'color':'#20221f','highlight':'#2b703e','bottom':.06}),
        Template(id='bold',name='Nhịp nhanh',background='#141414',viewport=Viewport(x=0,y=.13,w=1,h=.72),
            layers=[Layer(kind='rect',x=0,y=0,w=1,h=.13,color='#b5f36d',text=''),
                Layer(kind='text',x=.06,y=.025,w=.88,h=.095,text='{title}',size=58,color='#151515',animation='slide')],
            transition='fade',caption={'bottom':.06})]

def tokens(text):
    s=unicodedata.normalize('NFD',text.lower().replace('đ','d'))
    s=''.join(c for c in s if unicodedata.category(c)!='Mn')
    return set(re.findall(r'\w+',s))

def estimated_cues(script,duration):
    parts=sentences(script)
    if not parts: return []
    weights=[max(1,len(s.split())) for s in parts]; total=sum(weights); pos=0; out=[]
    for text,weight in zip(parts,weights):
        end=pos+duration*weight/total
        out.append(Cue(start=round(pos,3),end=round(end,3),text=text)); pos=end
    return out

def timed_words(cues):
    words=[]
    for cue in cues:
        parts=cue.text.split()
        weights=[max(1,len(w)) for w in parts]; total=sum(weights)
        pos=cue.start
        for word,weight in zip(parts,weights):
            end=pos+(cue.end-cue.start)*weight/total
            words.append(Cue(start=pos,end=end,text=word)); pos=end
    return words

def caption_groups(cues,max_words=6):
    words=timed_words(cues); groups=[]; current=[]
    for word in words:
        if current and (len(current)>=max_words or word.start-current[-1].end>.45 or re.search(r'[.!?。]$',current[-1].text)):
            groups.append(current); current=[]
        current.append(word)
    if current: groups.append(current)
    return groups

def plan(p,use_ai,job):
    pool=[(a,sc) for a in p.assets if a.role=='source' and not a.deleted for sc in a.scenes]
    if not pool: raise ValueError('Thêm tư liệu và bấm Phân tích cảnh trước khi lập timeline')
    duration=p.target_duration
    if p.voice_id:
        voice=get_asset(p,p.voice_id)
        duration=max(duration,voice.duration)
    if duration>180: raise ValueError('Video đầu ra tối đa 180 giây')
    if not p.cues or (p.cue_timing=='estimated' and not p.cues_edited and not p.cues_stale):
        p.cues=estimated_cues(p.script,duration); p.cue_timing='estimated'
        p.cues_edited=False; p.cues_stale=False
    if p.mode=='template' and p.template.slot_durations:
        ds=[max(.2,float(d)) for d in p.template.slot_durations]
        durations=[d*duration/sum(ds) for d in ds]
        if any(d<.2 for d in durations):
            raise ValueError('Mẫu có ô quá ngắn sau khi co thời lượng. Giảm số ô hoặc tăng thời lượng video.')
    else:
        # Snap cuts near speech word boundaries, keeping a preferred 2–3 second rhythm.
        n=max(1,round(duration/2.5)); boundaries=[0.0]
        ends=[w.end for w in timed_words(p.cues)]
        for i in range(1,n):
            ideal=duration*i/n
            candidates=[t for t in ends if abs(t-ideal)<.25 and t-boundaries[-1]>=1.8]
            boundaries.append(min(candidates,key=lambda t:abs(t-ideal)) if candidates else ideal)
        boundaries.append(duration)
        durations=[b-a for a,b in zip(boundaries,boundaries[1:])]
    texts=[]; position=0
    for d in durations:
        texts.append(' '.join(c.text for c in p.cues if c.start<position+d and c.end>position))
        position+=d
    ai_choices={}
    if use_ai:
        if any(not scene.ai_labeled for _,scene in pool):
            from .providers import label_scenes
            label_scenes(p,job)
        job.update(20,'AI chọn cảnh theo từng ý của kịch bản')
        catalog=[{'id':s.id,'tags':s.tags,'source':a.name,'length':round(s.end-s.start,2)} for a,s in pool]
        result=ai_json('Chọn cảnh phù hợp từng ý video, tránh lặp và giữ đúng đối tượng/hành động. JSON {"choices":[{"index":0,"scene_id":"..."}]}. Không bịa ID. Các ý: '+json.dumps(texts,ensure_ascii=False)+'\nThư viện: '+json.dumps(catalog,ensure_ascii=False))
        ai_choices={x.get('index'):x.get('scene_id') for x in result.get('choices',[]) if isinstance(x,dict)}
    used=Counter(); fingerprints=Counter(); previous=''; clips=[]
    for i,(d,text) in enumerate(zip(durations,texts)):
        job.check(); wanted=tokens(text)
        def score(pair):
            a,s=pair
            return (len(wanted & tokens(s.tags+' '+a.tags))*3 + s.quality*.2
                + (6 if ai_choices.get(i)==s.id else 0)
                - used[s.id]*4 - fingerprints[s.fingerprint]*1.5
                - (1 if previous==a.id else 0) - max(0,d-(s.end-s.start)))
        a,sc=max(pool,key=score)
        used[sc.id]+=1
        if sc.fingerprint: fingerprints[sc.fingerprint]+=1
        previous=a.id
        # Long template slots may extend with a frozen last frame; explicitly flag them.
        clips.append(Clip(asset_id=a.id,scene_id=sc.id,source_start=sc.start,duration=round(d,3),
            title=' '.join(text.split()[:9]) or p.name,transition=p.template.transition))
    # Correct rounding so the soundtrack and timeline share the same endpoint.
    clips[-1].duration=round(clips[-1].duration+duration-sum(c.duration for c in clips),3)
    p.clips=clips; p.warnings=[]
    if p.cue_timing=='estimated': p.warnings.append('Phụ đề đang ước lượng theo kịch bản. Tạo giọng hoặc căn lại từ file voice để khớp lời đọc.')
    if p.cues_stale: p.warnings.append('Kịch bản đã thay đổi; phụ đề cũ đang được giữ lại và cần được kiểm tra hoặc tạo lại.')
    if any(a.media=='video' and c.duration>next(s.end-s.start for aa,s in pool if aa.id==c.asset_id and s.id==c.scene_id)+.1 for c in clips for a in p.assets if a.id==c.asset_id):
        p.warnings.append('Một số ô dài hơn cảnh nguồn; khung cuối sẽ được giữ thêm. Bạn có thể thay cảnh hoặc rút thời lượng.')
    if not use_ai: p.warnings.append('Chọn cảnh theo nhãn/từ khóa và độ đa dạng. Bật AI để ghép theo ý nghĩa hình ảnh.')
    return p

def validate_timeline(p):
    if not p.clips: raise ValueError('Timeline chưa có cảnh')
    duration=sum(c.duration for c in p.clips)
    if duration>180.1: raise ValueError('Tổng timeline vượt 180 giây')
    for clip in p.clips:
        a=get_asset(p,clip.asset_id)
        if a.media not in ['video','image']: raise ValueError('Timeline chỉ nhận ảnh hoặc video')
        if a.media=='video' and clip.source_start>=a.duration-.01:
            raise ValueError('Mốc cắt vượt thời lượng nguồn: '+a.name)
    if p.voice_id and get_asset(p,p.voice_id).duration>duration+.15:
        raise ValueError('Timeline ngắn hơn voice. Kéo dài timeline hoặc lập lại để tránh cắt mất lời.')
    return duration
