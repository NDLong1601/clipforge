import os, subprocess, time, shutil, math
from pathlib import Path
import av, cv2, numpy as np
from PIL import Image
import imageio_ffmpeg
from .models import Asset, Scene, uid
from .store import project_dir, asset_path

def ffmpeg():
    return os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg') or imageio_ffmpeg.get_ffmpeg_exe()

def run_ff(args, cwd=None, job=None, timeout=1800):
    import tempfile
    # File-backed stderr avoids pipe deadlocks during long encodes.
    with tempfile.TemporaryFile() as err:
        process = subprocess.Popen([ffmpeg(),'-hide_banner','-nostdin','-y',*map(str,args)],
            cwd=cwd, stdout=subprocess.DEVNULL, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if job: job.check()
                if time.monotonic()-started>timeout:
                    raise ValueError('FFmpeg vượt thời gian xử lý cho phép')
                time.sleep(.15)
            if process.returncode:
                err.seek(0)
                tail = err.read().decode('utf-8',errors='replace')[-1800:]
                raise ValueError('Không xử lý được media: '+tail)
        except BaseException:
            process.kill()
            process.wait()
            raise

def probe(path):
    try:
        with Image.open(path) as im:
            if im.format in ['PNG','JPEG','WEBP','BMP']:
                return dict(media='image',duration=0,width=im.width,height=im.height,fps=0,has_audio=False)
    except Exception:
        pass
    try:
        with av.open(str(path)) as container:
            video = next(iter(container.streams.video), None)
            audio = next(iter(container.streams.audio), None)
            duration = (container.duration or 0) / av.time_base
            if not duration:
                streams = [s for s in [video,audio] if s and s.duration and s.time_base]
                duration = max((float(s.duration*s.time_base) for s in streams), default=0)
            if not video and not audio:
                raise ValueError('File không chứa hình ảnh hoặc âm thanh')
            if duration<=0 or duration>7200:
                raise ValueError('Tư liệu phải có thời lượng từ 0 đến 120 phút')
            return dict(media='video' if video else 'audio', duration=round(duration,3),
                width=video.width if video else 0,height=video.height if video else 0,
                fps=float(video.average_rate or 30) if video else 0, has_audio=bool(audio))
    except ValueError:
        raise
    except Exception as e:
        raise ValueError('Không đọc được file media. Hãy dùng MP4, MOV, MP3, WAV, PNG hoặc JPG.') from e

def thumbnail(path, dest, seconds=0, job=None):
    dest.parent.mkdir(parents=True,exist_ok=True)
    args = ['-ss',str(max(0,seconds)),'-i',path,'-frames:v','1','-vf','scale=360:-2',dest]
    run_ff(args,job=job)
    if not dest.exists():
        raise ValueError('Không tạo được ảnh xem trước')

def register(pid, path, name, role, job=None):
    info = probe(path)
    if role in ['voice','music'] and not info['has_audio']:
        raise ValueError('File này không chứa âm thanh')
    if role=='reference' and info['media']!='video':
        raise ValueError('Video mẫu phải là file video')
    if role=='overlay' and info['media']!='image':
        raise ValueError('Biểu tượng/logo cần là PNG, JPG hoặc WEBP')
    if role=='source' and info['media']=='audio':
        raise ValueError('Chọn vai trò Giọng đọc hoặc Nhạc nền cho file âm thanh')
    if role=='voice' and info['duration']>180:
        raise ValueError('Giọng đọc đầu ra tối đa 180 giây')
    a = Asset(name=name, filename=path.name, role=role, **info)
    if a.media!='audio':
        a.thumbnail = f'{a.id}.jpg'
        thumbnail(path,project_dir(pid)/'thumbs'/a.thumbnail,min(.2,a.duration/2),job)
    return a

def analyze(pid, asset, job=None):
    if asset.media=='image':
        asset.scenes = [Scene(start=0,end=3,thumbnail=asset.thumbnail,tags=asset.tags or asset.name)]
        return asset
    if asset.media!='video': return asset
    cap = cv2.VideoCapture(str(asset_path(pid,asset)))
    step = .4
    cuts = [0.0]
    prev = None
    pos = 0.0
    while pos<asset.duration:
        if job: job.check()
        cap.set(cv2.CAP_PROP_POS_MSEC,pos*1000)
        ok,frame = cap.read()
        if not ok: break
        small = cv2.resize(frame,(160,90))
        hsv = cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv],[0,1],None,[32,32],[0,180,0,256])
        cv2.normalize(hist,hist)
        if prev is not None and cv2.compareHist(prev,hist,cv2.HISTCMP_BHATTACHARYYA)>.55 and pos-cuts[-1]>=.8:
            cuts.append(round(pos,3))
        prev = hist
        pos += step
    cap.release()
    cuts.append(asset.duration)
    segments=[]
    for start,end in zip(cuts,cuts[1:]):
        length=end-start
        if length<.45: continue
        # Balanced windows do not cross a detected shot boundary.
        n=1 if asset.role=='reference' else max(1,math.ceil(length/3))
        for i in range(n):
            segments.append((start+length*i/n,start+length*(i+1)/n))
    scenes=[]
    for i,(start,end) in enumerate(segments):
        if job: job.check()
        sid=uid()
        thumb=f'{asset.id}_{sid}.jpg'
        thumbnail(asset_path(pid,asset),project_dir(pid)/'thumbs'/thumb,(start+end)/2,job)
        gray=cv2.imread(str(project_dir(pid)/'thumbs'/thumb),cv2.IMREAD_GRAYSCALE)
        tiny=cv2.resize(gray,(9,8))
        bits=(tiny[:,1:]>tiny[:,:-1]).flatten()
        fingerprint=f'{int("".join("1" if x else "0" for x in bits),2):016x}'
        quality=min(1,float(cv2.Laplacian(gray,cv2.CV_64F).var())/800)
        scenes.append(Scene(id=sid,start=round(start,3),end=round(end,3),thumbnail=thumb,
            tags=asset.tags or asset.name,quality=round(quality,3),fingerprint=fingerprint))
    asset.scenes=scenes
    return asset

def extract_audio(source,dest,job=None):
    run_ff(['-i',source,'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',dest],job=job)

def dimensions(aspect,resolution):
    s=int(resolution)
    return (s,int(s*16/9)//2*2) if aspect=='9:16' else ((int(s*16/9)//2*2,s) if aspect=='16:9' else (s,s))
