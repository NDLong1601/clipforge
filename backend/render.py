import math, textwrap, shutil, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .store import project_dir, get_asset, asset_path, now
from . import store
from .models import Cue
from .media import run_ff, dimensions, probe
from .planner import validate_timeline, caption_groups
from .preflight import preflight
from .audio_cache import normalized_audio

def font(size):
    for p in ['C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/System/Library/Fonts/Supplemental/Arial Bold.ttf']:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default(size=size)

def wrap_text(draw,text,fnt,width):
    lines=[]
    for paragraph in text.split('\n'):
        line=''
        for word in paragraph.split():
            new=(line+' '+word).strip()
            if draw.textbbox((0,0),new,font=fnt)[2]>width and line:
                lines.append(line); line=word
            else: line=new
        lines.append(line)
    return '\n'.join(lines)

def layer_image(p,layer,clip,width,height,path):
    lw,lh=max(2,round(layer.w*width)),max(2,round(layer.h*height))
    im=Image.new('RGBA',(lw,lh),(0,0,0,0)); draw=ImageDraw.Draw(im)
    if layer.kind in ['rect','circle']:
        fn=draw.rectangle if layer.kind=='rect' else draw.ellipse
        fn((0,0,lw-1,lh-1),fill=layer.color)
    elif layer.kind=='image':
        if not layer.asset_id: return None
        a=get_asset(p,layer.asset_id)
        with Image.open(asset_path(p.id,a)) as src:
            src=ImageOps.contain(src.convert('RGBA'),(lw,lh))
            im.alpha_composite(src,((lw-src.width)//2,(lh-src.height)//2))
    else:
        text=layer.text.replace('{title}',clip.title).replace('{project}',p.name).replace('{caption}',clip.caption)
        size=max(10,round(layer.size*width/1080))
        for fs in range(size,9,-1):
            fnt=font(fs); wrapped=wrap_text(draw,text,fnt,lw-8)
            box=draw.multiline_textbbox((0,0),wrapped,font=fnt,spacing=fs*.18)
            if box[3]-box[1]<=lh-4: break
        draw.multiline_text((2,-box[1]+2),wrapped,font=fnt,fill=layer.color,spacing=fs*.18,
            stroke_width=max(0,round(width/1080)),stroke_fill=layer.background)
    if layer.opacity<1: im.putalpha(im.getchannel('A').point(lambda x:int(x*layer.opacity)))
    im.save(path); return path

def ass_time(t):
    cs=round(max(0,t)*100); h,cs=divmod(cs,360000); m,cs=divmod(cs,6000); s,cs=divmod(cs,100)
    return f'{h}:{m:02}:{s:02}.{cs:02}'

def srt_time(t):
    ms=round(max(0,t)*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def safe_ass(text):
    return text.replace('\\','/').replace('{','(').replace('}',')').replace('\n',' ')

def ass_color(color):
    return '&H00'+color[5:7]+color[3:5]+color[1:3]

def subtitles(p,width,height,directory):
    overrides=[]; pos=0
    for clip in p.clips:
        if clip.caption: overrides.append(Cue(start=pos,end=pos+clip.duration,text=clip.caption))
        pos+=clip.duration
    base=[]
    for c in p.cues:
        ranges=[(c.start,c.end)]
        for override in overrides:
            ranges=[part for start,end in ranges for part in [(start,min(end,override.start)),(max(start,override.end),end)] if part[1]>part[0]]
        base.extend(Cue(start=s,end=e,text=c.text) for s,e in ranges if e>s)
    base.extend(overrides); base.sort(key=lambda c:c.start)
    style=p.template.caption
    groups=[]; pending=[]
    boundaries={c.start for c in overrides}|{c.end for c in overrides}
    for c in base:
        if pending and c.start in boundaries:
            groups.extend(caption_groups(pending,style.words_per_line));pending=[]
        pending.append(c)
    groups.extend(caption_groups(pending,style.words_per_line))
    srt=[]; events=[]
    for i,group in enumerate(groups):
        start,end=group[0].start,group[-1].end
        text=' '.join(w.text for w in group)
        srt.append(f'{i+1}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n')
        if style.karaoke:
            text=' '.join('{\\kf'+str(max(1,round((w.end-w.start)*100)))+'}'+safe_ass(w.text) for w in group)
        else: text=safe_ass(text)
        events.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}')
    size=round(style.font_size*width/1080); margin=round(style.bottom*height)
    rgb=[int(style.color[i:i+2],16) for i in (1,3,5)]
    dark=sum(rgb)<360
    outline='&H00FFFFFF' if dark else '&H00101010'
    header=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{size},{ass_color(style.highlight if style.karaoke else style.color)},{ass_color(style.color)},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{max(1,round(width/540))},{0 if dark else 1},2,{round(width*.06)},{round(width*.06)},{margin},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    (directory/'captions.ass').write_text(header+'\n'.join(events),encoding='utf-8-sig')
    (directory/'captions.srt').write_text('\n'.join(srt),encoding='utf-8-sig')

def render(p,job,preview=False):
    report=preflight(p,project_dir(p.id))
    errors=[issue for issue in report['issues'] if issue['severity']=='error']
    if errors:
        first=errors[0]
        raise ValueError(f"Preflight {first['code']}: {first['message']} {first['fix']}")
    duration=validate_timeline(p)
    d=project_dir(p.id)/'exports'/job.id; d.mkdir(parents=True,exist_ok=True)
    width,height=dimensions(p.aspect,'720' if preview else p.resolution)
    fps=30; vp=p.template.viewport
    vw=max(2,int(width*vp.w)//2*2); vh=max(2,int(height*vp.h)//2*2)
    vx=min(width-vw,round(width*vp.x)); vy=min(height-vh,round(height*vp.y))
    for i,clip in enumerate(p.clips):
        job.update(5+70*i/len(p.clips),f'Dựng cảnh {i+1}/{len(p.clips)}')
        a=get_asset(p,clip.asset_id); source=asset_path(p.id,a)
        args=[]
        if a.media=='image': args+=['-loop','1','-i',source]
        else:
            available=a.duration-clip.source_start
            if clip.scene_id:
                sc=next((s for s in a.scenes if s.id==clip.scene_id),None)
                if sc and clip.source_start<sc.end: available=min(available,sc.end-clip.source_start)
            args+=['-ss',clip.source_start,'-t',min(available,clip.duration*clip.speed),'-i',source]
        if clip.fit=='cover':
            scale=f'scale={vw}:{vh}:force_original_aspect_ratio=increase,crop={vw}:{vh}:(iw-ow)*{clip.crop_x}:(ih-oh)*{clip.crop_y}'
        else:
            scale=f'scale={vw}:{vh}:force_original_aspect_ratio=decrease,pad={vw}:{vh}:(ow-iw)/2:(oh-ih)/2:color={p.template.background}'
        filters=[f'[0:v]setpts=(PTS-STARTPTS)/{clip.speed},{scale},setsar=1,fps={fps},tpad=stop_mode=clone:stop_duration={clip.duration},trim=duration={clip.duration},pad={width}:{height}:{vx}:{vy}:color={p.template.background}[base]']
        layers=list(p.template.layers)
        if p.mode=='template' and p.template.slot_layers:
            layers+=p.template.slot_layers[i%len(p.template.slot_layers)]
        layers+=clip.layers
        last='base'; index=0
        for layer in layers:
            path=d/f'layer_{i}_{index}.png'
            if not layer_image(p,layer,clip,width,height,path): continue
            index+=1; args+=['-loop','1','-i',path]
            label=f'ov{index}'; plane=f'plane{index}'
            filters.append(f'[{index}:v]format=rgba'+(',fade=t=in:st=0:d=0.25:alpha=1' if layer.animation=='fade' else '')+f'[{plane}]')
            x=str(round(layer.x*width)); y=round(layer.y*height)
            if layer.animation=='slide': x=f"'{x}-{round(layer.w*width)}*max(0,1-t/0.25)'"
            filters.append(f'[{last}][{plane}]overlay=x={x}:y={y}:shortest=1[{label}]'); last=label
        end='format=yuv420p'
        if clip.transition=='fade':
            fade=min(.18,clip.duration/4)
            end+=f',fade=t=in:d={fade},fade=t=out:st={clip.duration-fade}:d={fade}'
        filters.append(f'[{last}]{end}[out]')
        args+=['-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','[out]','-an','-t',clip.duration,
            '-c:v','libx264','-preset','veryfast','-crf','24' if preview else '20','-threads','2',d/f'clip_{i:03d}.mp4']
        run_ff(args,cwd=d,job=job)
    job.update(78,'Ghép timeline và phụ đề')
    (d/'list.txt').write_text('\n'.join(f"file 'clip_{i:03d}.mp4'" for i in range(len(p.clips))),encoding='utf-8')
    run_ff(['-f','concat','-safe','0','-i','list.txt','-c','copy','joined.mp4'],cwd=d,job=job)
    subtitles(p,width,height,d)
    args=['-i','joined.mp4']; filters=[]; idx=1; voice_idx=None; music_idx=None
    voice_active=bool(p.voice_id and p.voice_volume>0)
    music_active=bool(p.music_id and p.music_volume>0)
    if voice_active:
        voice_idx=idx;idx+=1
        source=asset_path(p.id,get_asset(p,p.voice_id))
        cached=normalized_audio(source,store.ROOT/'cache',job)
        args+=['-i',cached]
        filters.append(f'[{voice_idx}:a]aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[voice_raw]')
    if music_active:
        music_idx=idx;idx+=1
        source=asset_path(p.id,get_asset(p,p.music_id))
        cached=normalized_audio(source,store.ROOT/'cache',job)
        args+=['-stream_loop','-1','-i',cached]
        filters.append(f'[{music_idx}:a]aresample=48000,atrim=duration={duration},asetpts=PTS-STARTPTS[music_raw]')
    limiter='alimiter=limit=0.95:attack=5:release=50:level=disabled'
    music_fade=f',afade=t=out:st={max(0,duration-1)}:d={min(1,duration)}'
    if voice_idx and music_idx:
        if p.ducking:
            filters += [
                '[voice_raw]asplit=2[voice_gain_src][voice_side]',
                '[music_raw][voice_side]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[music_ducked]',
                f'[voice_gain_src]volume={p.voice_volume}[voice]',
                f'[music_ducked]volume={p.music_volume}{music_fade}[music]',
                f'[voice][music]amix=inputs=2:duration=longest:normalize=0,{limiter}[audio]',
            ]
        else:
            filters += [
                f'[voice_raw]volume={p.voice_volume}[voice]',
                f'[music_raw]volume={p.music_volume}{music_fade}[music]',
                f'[voice][music]amix=inputs=2:duration=longest:normalize=0,{limiter}[audio]',
            ]
    elif voice_idx:
        filters.append(f'[voice_raw]volume={p.voice_volume},{limiter}[audio]')
    elif music_idx:
        filters.append(f'[music_raw]volume={p.music_volume}{music_fade},{limiter}[audio]')
    else:
        args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']; filters.append(f'[{idx}:a]atrim=duration={duration}[audio]')
    filters.append('[0:v]'+('subtitles=captions.ass' if p.template.caption.enabled and (p.cues or any(c.caption for c in p.clips)) else 'null')+'[video]')
    output='preview.mp4' if preview else 'video.mp4'
    args+=['-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','[video]','-map','[audio]',
        '-t',duration,'-c:v','libx264','-preset','veryfast','-crf','24' if preview else '20','-pix_fmt','yuv420p',
        '-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-threads','2',output]
    job.update(86,'Trộn voice, nhạc và xuất MP4');run_ff(args,cwd=d,job=job)
    info=probe(d/output)
    if abs(info['duration']-duration)>.3: raise ValueError('Thời lượng xuất không khớp timeline')
    snapshot=p.model_dump(); (d/'project.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    # Keep deliverables; intermediate encodes can be large and are reproducible.
    for path in d.iterdir():
        if path.name not in [output,'captions.srt','captions.ass','project.json']: path.unlink()
    return {'id':job.id,'filename':output,'duration':round(info['duration'],2),'width':width,'height':height,
        'created_at':now(),'preview':preview,'size':(d/output).stat().st_size}
