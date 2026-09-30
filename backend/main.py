import json, re, shutil, os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError
from . import store, jobs, media, providers, planner, render
from .preflight import preflight
from . import template_packages
from .template_packages import MissingTemplateAssets
from .models import Project, Action, Template, Clip, uid

@asynccontextmanager
async def lifespan(_app):
    store.acquire_instance_lock()
    try:
        yield
    finally:
        store.release_instance_lock()

app=FastAPI(title='ClipForge Local',version='1.0.0',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

@app.middleware('http')
async def same_origin(request:Request,call_next):
    if request.method not in ['GET','HEAD','OPTIONS']:
        origin=request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Chỉ chấp nhận thao tác từ giao diện cùng máy/chung địa chỉ.'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='DENY'
    return response

@app.exception_handler(ValueError)
async def value_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=400)

@app.exception_handler(FileNotFoundError)
async def missing(request,exc):
    return JSONResponse({'detail':'Không tìm thấy dự án hoặc file.'},status_code=404)

def unlocked(pid):
    if jobs.busy(pid): raise HTTPException(409,'Dự án đang xử lý. Chờ hoàn tất hoặc hủy tác vụ.')

def task(pid,kind,operation):
    store.read(pid)
    def perform(job):
        p=store.read(pid)
        p=operation(p,job)
        job.check(); store.save(p)
        return {'project_id':p.id,'revision':p.revision}
    return jobs.submit(pid,kind,perform)

@app.get('/api/health')
def health():
    return {'status':'ok','product':'clipforge-local','ffmpeg':Path(media.ffmpeg()).name,'version':'1.0.0'}

@app.get('/api/settings')
def settings(): return store.public_settings()

@app.put('/api/settings')
def set_settings(data:dict): return store.save_settings(data)

@app.post('/api/settings/use-environment')
def use_environment_secret(data:dict): return store.use_environment_secret(data.get('secret'))

@app.get('/api/voices/windows')
def windows_voices():
    if os.name!='nt': return []
    import subprocess
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; @($s.GetInstalledVoices() | ForEach-Object { @{name=$_.VoiceInfo.Name; culture=$_.VoiceInfo.Culture.Name} }) | ConvertTo-Json -Compress"],capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        data=json.loads(result.stdout.decode('utf-8-sig'));return data if isinstance(data,list) else [data]
    except Exception: return []

@app.post('/api/settings/test')
def test_ai(data:dict|None=None):
    data=data or {}
    result,route=providers.ai_json('Trả JSON {"message":"Kết nối AI thành công"}.',
        profile_id=data.get('profile_id'),allow_fallback=bool(data.get('allow_fallback',False)),
        return_route=True)
    return {'message':result.get('message','Kết nối AI thành công'),'route':route}

@app.get('/api/projects')
def list_projects(): return store.projects()

@app.post('/api/projects')
def create_project(data:dict):
    p=Project(name=data.get('name','Video mới'),mode=data.get('mode','remix'),template=planner.presets()[0])
    return store.save(p)

@app.get('/api/projects/{pid}')
def get_project(pid:str): return store.read(pid)

@app.get('/api/projects/{pid}/preflight')
def project_preflight(pid:str):
    p=store.read(pid)
    return preflight(p,store.project_dir(pid))

@app.post('/api/projects/{pid}/recover')
def recover_project(pid:str):
    with jobs.LOCK,store.LOCK:
        unlocked(pid)
        return store.recover(pid)

@app.get('/api/projects/{pid}/schema-backups')
def schema_backups(pid:str):
    return store.schema_backup_info(pid)

@app.get('/api/projects/{pid}/schema-backups/{backup_id}/download')
def download_schema_backup(pid:str,backup_id:str):
    archive=store.create_schema_backup_archive(pid,backup_id)
    return FileResponse(archive,filename=f'clipforge-{pid}-{backup_id}.zip',
                        media_type='application/zip',
                        background=BackgroundTask(lambda: archive.unlink(missing_ok=True)))

@app.put('/api/projects/{pid}')
def update_project(pid:str,p:Project):
    with jobs.LOCK,store.LOCK:
        unlocked(pid); old=store.read(pid)
        if p.id!=pid: raise ValueError('ID dự án không khớp')
        if p.revision!=old.revision: raise HTTPException(409,'Dự án đã thay đổi. Tải lại trước khi lưu.')
        # File locations are server-owned. Users may edit source tags, never filenames.
        tags={a.id:a for a in p.assets}
        for a in old.assets:
            if a.id in tags:
                a.tags=tags[a.id].tags[:1500]
                stags={s.id:s.tags for s in tags[a.id].scenes}
                for s in a.scenes: s.tags=stags.get(s.id,s.tags)[:1500]
        p.assets=old.assets;p.exports=old.exports
        cue_reviewed=bool(p.cues_edited and not p.cues_stale and p.cues!=old.cues)
        if p.script!=old.script:
            p.voice_id='';p.cues_stale=bool(p.cues) and not cue_reviewed
            p.warnings=['Kịch bản đã đổi. Chọn lại hoặc tạo lại voice trước khi xuất.']
            if p.cues_stale:p.warnings.append('Phụ đề cũ được giữ nguyên; hãy kiểm tra hoặc chọn Tạo lại mốc phụ đề.')
        elif p.voice_id!=old.voice_id:
            if not p.cues_edited:
                p.cues=planner.estimated_cues(p.script,max(p.target_duration,store.get_asset(p,p.voice_id).duration if p.voice_id else 0));p.cue_timing='estimated'
                p.cues_stale=False
            else:
                p.cues_stale=old.cues_stale and not cue_reviewed
        else:
            p.cues_stale=False if cue_reviewed else old.cues_stale
        if cue_reviewed:
            p.warnings=[warning for warning in p.warnings if 'phụ đề cũ được giữ nguyên' not in warning.lower()]
        if p.voice_id: store.get_asset(p,p.voice_id)
        if p.music_id: store.get_asset(p,p.music_id)
        for c in p.clips:
            a=store.get_asset(p,c.asset_id)
            if a.media=='audio':raise ValueError('Không dùng âm thanh làm cảnh hình')
        return store.save(p)

@app.post('/api/projects/{pid}/cues/regenerate')
def regenerate_cues(pid:str,data:dict):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        if data.get('revision')!=p.revision:
            raise HTTPException(409,'Dự án đã thay đổi. Tải lại trước khi tạo lại phụ đề.')
        duration=max(p.target_duration,store.get_asset(p,p.voice_id).duration if p.voice_id else 0)
        p.cues=planner.estimated_cues(p.script,duration)
        p.cue_timing='estimated';p.cues_edited=False;p.cues_stale=False
        p.warnings=[warning for warning in p.warnings
                    if 'phụ đề cũ được giữ nguyên' not in warning.lower()
                    and not (p.voice_id and warning.lower().startswith('kịch bản đã đổi'))]
        return store.save(p)

@app.post('/api/projects/{pid}/cues/confirm')
def confirm_cues(pid:str,data:dict):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        if data.get('revision')!=p.revision:
            raise HTTPException(409,'Dự án đã thay đổi. Tải lại trước khi xác nhận phụ đề.')
        if not p.cues:
            raise ValueError('Chưa có phụ đề để xác nhận.')
        p.cues_edited=True;p.cues_stale=False
        p.warnings=[warning for warning in p.warnings if 'phụ đề cũ được giữ nguyên' not in warning.lower()]
        return store.save(p)

@app.post('/api/projects/{pid}/undo')
def undo(pid:str):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);return store.undo(pid)

@app.post('/api/projects/{pid}/assets')
async def upload(pid:str,files:list[UploadFile]=File(...),role:str=Form('source')):
    if role not in ['source','reference','voice','music','overlay']:raise ValueError('Loại tư liệu không hợp lệ')
    if len(files)>10:raise ValueError('Mỗi lần nhập tối đa 10 file')
    paths=[];assets=[]
    # Upload is guarded as a project operation so background tasks cannot overwrite it.
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        directory=store.project_dir(pid)/'assets';directory.mkdir(parents=True,exist_ok=True)
        try:
            for file in files:
                suffix=Path(file.filename or '').suffix.lower()
                if suffix not in ['.mp4','.mov','.mkv','.webm','.avi','.m4v','.mp3','.wav','.m4a','.aac','.flac','.ogg','.png','.jpg','.jpeg','.webp','.bmp']:
                    raise ValueError('Định dạng file chưa được hỗ trợ')
                path=directory/(uid()+suffix); paths.append(path); total=0
                with path.open('wb') as output:
                    while chunk:=file.file.read(1024*1024):
                        total+=len(chunk)
                        if total>2*1024**3:raise ValueError('Mỗi file tối đa 2 GB')
                        output.write(chunk)
                a=media.register(pid,path,Path(file.filename).name,role)
                assets.append(a)
            p.assets+=assets
            if role=='voice':
                p.voice_id=assets[-1].id;p.cue_timing='estimated'
                p.cues=planner.estimated_cues(p.script,assets[-1].duration)
                p.cues_edited=False;p.cues_stale=False
                p.target_duration=min(180,max(p.target_duration,assets[-1].duration))
            if role=='music':p.music_id=assets[-1].id
            store.save(p)
        except BaseException:
            for path in paths:path.unlink(missing_ok=True)
            raise
    return p

@app.delete('/api/projects/{pid}/assets/{aid}')
def remove_asset(pid:str,aid:str):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        asset=store.get_asset(p,aid,include_deleted=False)
        asset.deleted=True
        p.clips=[clip for clip in p.clips if clip.asset_id!=aid]
        for layers in [p.template.layers,*p.template.slot_layers,*(clip.layers for clip in p.clips)]:
            for layer in layers:
                if layer.asset_id==aid: layer.asset_id=''
        if p.voice_id==aid:p.voice_id=''
        if p.music_id==aid:p.music_id=''
        return store.save(p)

@app.get('/api/projects/{pid}/assets/{aid}/file')
def asset_file(pid:str,aid:str):
    p=store.read(pid);a=store.get_asset(p,aid)
    return FileResponse(store.asset_path(pid,a))

@app.get('/api/projects/{pid}/thumbs/{name}')
def thumb(pid:str,name:str):
    if not re.fullmatch(r'[a-zA-Z0-9_]+\.jpg',name):raise ValueError('Tên ảnh không hợp lệ')
    return FileResponse(store.project_dir(pid)/'thumbs'/name)

@app.post('/api/projects/{pid}/analyze')
def analyze(pid:str,action:Action):
    def op(p,job):
        sources=[a for a in p.assets if a.role=='source' and not a.deleted]
        if not sources:raise ValueError('Chưa có video/ảnh nguồn')
        for i,a in enumerate(sources):
            job.update(5+30*i/len(sources),'Tách cảnh: '+a.name)
            if not a.scenes:media.analyze(pid,a,job)
        if action.use_ai:providers.label_scenes(p,job)
        return p
    return task(pid,'analyze',op)

@app.post('/api/projects/{pid}/plan')
def plan(pid:str,action:Action):return task(pid,'plan',lambda p,j:planner.plan(p,action.use_ai,j))

@app.post('/api/projects/{pid}/voice')
def voice(pid:str):return task(pid,'voice',providers.synthesize)

@app.post('/api/projects/{pid}/transcribe')
def transcribe(pid:str,action:Action):
    return task(pid,'transcribe',lambda p,j:providers.transcribe(p,store.get_asset(p,action.asset_id or p.voice_id),j))

@app.get('/api/templates')
def templates():
    saved=[]
    for path in (store.ROOT/'templates').glob('*.json'):
        saved.append(Template.model_validate_json(path.read_text(encoding='utf-8')))
    return planner.presets()+saved

@app.get('/api/template-packages')
def template_packages_list(): return template_packages.list_packages()

@app.post('/api/projects/{pid}/templates')
def save_project_template(pid:str,t:Template):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        return template_packages.save_package(p,t)

@app.post('/api/projects/{pid}/templates/{template_id}/apply')
def apply_project_template(pid:str,template_id:str,data:dict|None=None):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        try:
            return template_packages.apply_package(p,template_id,(data or {}).get('asset_map',{}))
        except MissingTemplateAssets as exc:
            raise HTTPException(409,detail={'message':str(exc),'missing_assets':exc.missing}) from exc

@app.post('/api/templates')
def save_template(t:Template):
    if any(layer.kind=='image' and layer.asset_id for layer in [*t.layers,*(x for group in t.slot_layers for x in group)]):
        raise ValueError('Template có ảnh cần được lưu từ dự án để đóng gói tài nguyên.')
    t.id=uid();store.atomic(store.ROOT/'templates'/f'{t.id}.json',t.model_dump());return t

@app.post('/api/templates/validate')
def validate_template(t:Template):return t

@app.post('/api/projects/{pid}/template')
def make_template(pid:str,action:Action):
    def op(p,job):
        a=store.get_asset(p,action.asset_id)
        if a.role!='reference':raise ValueError('Chọn một video mẫu')
        job.update(10,'Phân tích nhịp cắt của video mẫu');media.analyze(pid,a,job)
        if action.use_ai:p.template=providers.infer_template(p,a,job)
        else:
            from .template_utils import compressed_slot_durations
            p.template=planner.presets()[0].model_copy(deep=True)
            p.template.name='Nhịp mẫu — '+a.name
            p.template.slot_durations=compressed_slot_durations(a)
            simplified=len(a.scenes)>100
            p.template.notes=('Đã gộp nhịp liên tiếp để vừa giới hạn 100 ô; toàn bộ cảnh gốc và thời lượng video mẫu vẫn được giữ. ' if simplified else '')+'Đã lấy nhịp cắt. Chỉnh khung/lớp thủ công hoặc bật AI để nhận diện bố cục.'
        p.mode='template';return p
    return task(pid,'template',op)

@app.post('/api/projects/{pid}/render')
def export(pid:str,action:Action):
    current=store.read(pid)
    report=preflight(current,store.project_dir(pid))
    if not report['ok']:
        raise HTTPException(400,detail=report)
    def op(p,job):
        info=render.render(p,job,action.preview);p.exports.append(info);return p
    return task(pid,'render',op)

@app.get('/api/projects/{pid}/exports/{eid}/{name}')
def export_file(pid:str,eid:str,name:str):
    p=store.read(pid)
    if not any(e['id']==eid for e in p.exports):raise HTTPException(404,'Bản xuất chưa hoàn tất')
    if name not in ['video.mp4','preview.mp4','captions.srt','captions.ass','project.json']:raise HTTPException(404)
    path=store.project_dir(pid)/'exports'/eid/name
    return FileResponse(path,filename=name if name not in ['video.mp4','preview.mp4'] else None)

@app.get('/api/projects/{pid}/download')
def download_project(pid:str):
    return JSONResponse(store.read(pid).model_dump(),headers={'Content-Disposition':'attachment; filename="project.json"'})

@app.post('/api/projects/{pid}/import-edit')
def import_edit(pid:str,data:Project):
    old=store.read(pid);data.id=pid;data.revision=old.revision
    return update_project(pid,data)

@app.get('/api/jobs')
def list_jobs(pid:str|None=None):return jobs.all_jobs(pid)

@app.post('/api/jobs/{jid}/cancel')
def cancel(jid:str):
    j=jobs.JOBS.get(jid)
    if not j:raise HTTPException(404,'Không tìm thấy tác vụ')
    j.cancelled.set();return j.dump()

@app.post('/api/projects/{pid}/assistant')
def assistant(pid:str,action:Action):
    def work(job):
        job.update(10,'AI đang đề xuất chỉnh sửa');p=store.read(pid)
        result=providers.assistant(p,action.prompt);result['revision']=p.revision;return result
    return jobs.submit(pid,'assistant',work)

@app.post('/api/projects/{pid}/apply-ai')
def apply_ai(pid:str,data:dict):
    with jobs.LOCK,store.LOCK:
        unlocked(pid);p=store.read(pid)
        if 'revision' in data and data['revision']!=p.revision:
            raise ValueError('Dự án đã thay đổi từ lúc AI đề xuất. Hãy gửi lại yêu cầu AI.')
        if data.get('script') is not None:p.script=str(data['script'])
        if data.get('music_volume') is not None:p.music_volume=float(data['music_volume'])
        changes={c['id']:c for c in data.get('clip_changes',[]) if isinstance(c,dict) and 'id' in c}
        if set(changes)-{c.id for c in p.clips}:
            raise ValueError('Đề xuất AI chứa ID cảnh không tồn tại. Hãy tạo lại đề xuất.')
        for i,c in enumerate(p.clips):
            if c.id in changes:
                values=c.model_dump()
                values.update({k:v for k,v in changes[c.id].items() if k in ['title','caption','asset_id','source_start','duration']})
                if values['asset_id']!=c.asset_id:values['scene_id']=''
                p.clips[i]=Clip.model_validate(values)
        return update_project(pid,Project.model_validate(p.model_dump()))

@app.post('/api/demo')
def create_demo():
    from .demo import build_demo
    p=store.save(Project(name='Một chút bình yên',script='Đôi khi, điều bạn cần chỉ là một chuyến đi ngắn. Thức dậy giữa những ngọn đồi xanh. Lắng nghe tiếng sóng và để những lo âu trôi xa. Đi chậm qua một con phố mới. Ngắm bầu trời đổi màu khi chiều xuống. Hãy dành cho mình một khoảng lặng. Chuyến đi tiếp theo đang chờ bạn.',template=planner.presets()[0]))
    def op(job):
        fresh=build_demo(store.read(p.id),job);job.check();store.save(fresh);return {'project_id':p.id}
    return {'project':p,'job':jobs.submit(p.id,'demo',op)}

FRONTEND=Path(__file__).resolve().parents[1]/'frontend'/'dist'
if FRONTEND.exists():
    app.mount('/',StaticFiles(directory=FRONTEND,html=True),name='frontend')
