import json, re, shutil, os, tempfile, secrets, hashlib, time
import anyio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError
from . import (
    store,
    jobs,
    media,
    providers,
    planner,
    render,
    audio_assembly,
    face_filter,
)
from .preflight import preflight
from . import template_packages
from . import portable
from .template_packages import MissingTemplateAssets
from .models import Project, Action, Template, Clip, uid, AIApplyRequest, AIApplyCommit
from .project_validation import validate_project
from .font_manager import available as available_fonts
from . import sound_effects
from .template_content import bind_placeholders, infer_product


@asynccontextmanager
async def lifespan(_app):
    store.acquire_instance_lock()
    try:
        jobs.ROOT = store.ROOT
        jobs.cleanup_staging()
        jobs.initialize()
        yield
    finally:
        store.release_instance_lock()


app = FastAPI(title="ClipForge Local", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
)


@app.middleware("http")
async def same_origin(request: Request, call_next):
    if request.method == "POST" and request.url.path in {"/api/projects/import"}:
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > portable.MAX_ARCHIVE_BYTES + 32 * 1024**2:
            return JSONResponse(
                {"detail": "File backup vượt giới hạn dung lượng tải lên."},
                status_code=413,
            )
    if request.method == "POST" and re.fullmatch(
        r"/api/projects/[a-f0-9]{16}/assets", request.url.path
    ):
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > 5 * 1024**3 + 16 * 1024**2:
            return JSONResponse(
                {"detail": "Tổng dung lượng mỗi lần nhập tối đa 5 GB."}, status_code=413
            )
    if request.method not in ["GET", "HEAD", "OPTIONS"]:
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(
                {
                    "detail": "Chỉ chấp nhận thao tác từ giao diện cùng máy/chung địa chỉ."
                },
                status_code=403,
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(FileNotFoundError)
async def missing(request, exc):
    return JSONResponse({"detail": "Không tìm thấy dự án hoặc file."}, status_code=404)


def unlocked(pid):
    if jobs.busy(pid):
        raise HTTPException(409, "Dự án đang xử lý. Chờ hoàn tất hoặc hủy tác vụ.")


def task(pid, kind, operation):
    store.read(pid)

    def perform(job):
        p = store.read(pid)
        p = operation(p, job)
        job.check()
        job.commit(lambda: store.save(p))
        return {"project_id": p.id, "revision": p.revision}

    return jobs.submit(pid, kind, perform)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "product": "clipforge-local",
        "ffmpeg": Path(media.ffmpeg()).name,
        "version": "1.0.0",
    }


@app.get("/api/settings")
def settings():
    return store.public_settings()


@app.put("/api/settings")
def set_settings(data: dict):
    return store.save_settings(data)


@app.get("/api/fonts")
def fonts():
    return available_fonts()


@app.post("/api/settings/use-environment")
def use_environment_secret(data: dict):
    return store.use_environment_secret(data.get("secret"))


@app.get("/api/voices/windows")
def windows_voices():
    if os.name != "nt":
        return []
    import subprocess

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; @($s.GetInstalledVoices() | ForEach-Object { @{name=$_.VoiceInfo.Name; culture=$_.VoiceInfo.Culture.Name} }) | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        data = json.loads(result.stdout.decode("utf-8-sig"))
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


@app.post("/api/settings/test")
def test_ai(data: dict | None = None):
    data = data or {}
    result, route = providers.ai_json(
        'Trả JSON {"message":"Kết nối AI thành công"}.',
        profile_id=data.get("profile_id"),
        allow_fallback=bool(data.get("allow_fallback", False)),
        return_route=True,
    )
    return {"message": result.get("message", "Kết nối AI thành công"), "route": route}


@app.get("/api/projects")
def list_projects():
    return store.projects()


@app.post("/api/projects")
def create_project(data: dict):
    p = Project(
        name=data.get("name", "Video mới"),
        mode=data.get("mode", "remix"),
        template=planner.presets()[0],
        smooth_transitions=True,
    )
    return store.save(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return sound_effects.refresh(store.read(pid))


@app.get("/api/projects/{pid}/preflight")
def project_preflight(pid: str):
    p = store.read(pid)
    return preflight(p, store.project_dir(pid))


@app.post("/api/projects/{pid}/recover")
def recover_project(pid: str):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        return store.recover(pid)


@app.get("/api/projects/{pid}/schema-backups")
def schema_backups(pid: str):
    return store.schema_backup_info(pid)


@app.get("/api/projects/{pid}/schema-backups/{backup_id}/download")
def download_schema_backup(pid: str, backup_id: str):
    archive = store.create_schema_backup_archive(pid, backup_id)
    return FileResponse(
        archive,
        filename=f"clipforge-{pid}-{backup_id}.zip",
        media_type="application/zip",
        background=BackgroundTask(lambda: archive.unlink(missing_ok=True)),
    )


@app.get("/api/projects/{pid}/backup")
def download_project_backup(
    pid: str, include_exports: bool = False, include_history: bool = False
):
    archive = portable.create_backup(pid, include_exports, include_history)
    return FileResponse(
        archive,
        filename=f"clipforge-{pid}-backup.zip",
        media_type="application/zip",
        background=BackgroundTask(lambda: archive.unlink(missing_ok=True)),
    )


@app.post("/api/projects/import")
async def import_project_backup(file: UploadFile = File(...)):
    staging = store.ROOT / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix="upload-backup-", suffix=".zip", dir=staging, delete=False
    )
    archive = Path(handle.name)
    handle.close()
    copied = 0
    try:
        with archive.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                copied += len(chunk)
                if copied > portable.MAX_ARCHIVE_BYTES:
                    raise ValueError("File backup vượt giới hạn dung lượng tải lên.")
                await anyio.to_thread.run_sync(output.write, chunk)
        if copied == 0:
            raise ValueError("File ZIP đang trống.")
        project = await anyio.to_thread.run_sync(portable.import_backup, archive)
        return project
    finally:
        archive.unlink(missing_ok=True)
        await file.close()


@app.get("/api/storage")
def storage_report():
    return portable.storage_report()


@app.get("/api/storage/cleanup-preview")
def storage_cleanup_preview():
    return portable.cleanup_preview()


@app.post("/api/storage/cleanup-cache")
def storage_cleanup_cache():
    return portable.cleanup_cache()


@app.get("/api/trash")
def trash_list():
    return portable.list_trash()


@app.post("/api/trash/{trash_id}/restore")
def trash_restore(trash_id: str):
    return portable.restore_project(trash_id)


@app.delete("/api/trash/{trash_id}")
def trash_delete(trash_id: str):
    return portable.permanently_delete_trash(trash_id)


@app.post("/api/projects/{pid}/trash")
def trash_project(pid: str):
    return portable.trash_project(pid)


@app.put("/api/projects/{pid}")
def update_project(pid: str, p: Project, request: Request):
    operation_id = request.headers.get("x-history-operation", "")
    if not re.fullmatch(r"[a-f0-9-]{1,64}", operation_id):
        operation_id = ""
    return _update_project(pid, p, operation_id or None)


def _update_project(pid: str, p: Project, operation_id=None):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        old = store.read(pid)
        if p.id != pid:
            raise ValueError("ID dự án không khớp")
        if p.revision != old.revision:
            raise HTTPException(409, "Dự án đã thay đổi. Tải lại trước khi lưu.")
        return store.save(_normalize_project_update(old, p), operation_id=operation_id)


def _normalize_project_update(old: Project, submitted: Project) -> Project:
    """Prepare exactly the state to save, without mutating the persisted base."""
    p = submitted.model_copy(deep=True)
    canonical = old.model_copy(deep=True)
    # File locations are server-owned. Users may edit source tags, never filenames.
    tags = {a.id: a for a in p.assets}
    for a in canonical.assets:
        if a.id in tags:
            a.tags = tags[a.id].tags[:1500]
            stags = {s.id: s.tags for s in tags[a.id].scenes}
            for s in a.scenes:
                s.tags = stags.get(s.id, s.tags)[:1500]
    p.assets = canonical.assets
    p.exports = canonical.exports
    cue_reviewed = bool(p.cues_edited and not p.cues_stale and p.cues != old.cues)
    if p.script != old.script:
        p.voice_id = ""
        p.cues_stale = bool(p.cues) and not cue_reviewed
        p.warnings = ["Kịch bản đã đổi. Chọn lại hoặc tạo lại voice trước khi xuất."]
        if p.cues_stale:
            p.warnings.append(
                "Phụ đề cũ được giữ nguyên; hãy kiểm tra hoặc chọn Tạo lại mốc phụ đề."
            )
    elif p.voice_id != old.voice_id:
        if not p.cues_edited:
            p.cues = planner.estimated_cues(
                p.script,
                max(
                    p.target_duration,
                    store.get_asset(p, p.voice_id).duration if p.voice_id else 0,
                ),
            )
            p.cue_timing = "estimated"
            p.cues_stale = False
        else:
            p.cues_stale = old.cues_stale and not cue_reviewed
    else:
        p.cues_stale = False if cue_reviewed else old.cues_stale
    if cue_reviewed:
        p.warnings = [
            warning
            for warning in p.warnings
            if "phụ đề cũ được giữ nguyên" not in warning.lower()
        ]
    if p.voice_id:
        store.get_asset(p, p.voice_id)
    if p.music_id:
        store.get_asset(p, p.music_id)
    for c in p.clips:
        if store.get_asset(p, c.asset_id).media == "audio":
            raise ValueError("Không dùng âm thanh làm cảnh hình")
    bind_placeholders(p.template)
    return sound_effects.refresh(p)


@app.post("/api/projects/{pid}/cues/regenerate")
def regenerate_cues(pid: str, data: dict):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        p = store.read(pid)
        if data.get("revision") != p.revision:
            raise HTTPException(
                409, "Dự án đã thay đổi. Tải lại trước khi tạo lại phụ đề."
            )
        duration = max(
            p.target_duration,
            store.get_asset(p, p.voice_id).duration if p.voice_id else 0,
        )
        p.cues = planner.estimated_cues(p.script, duration)
        p.cue_timing = "estimated"
        p.cues_edited = False
        p.cues_stale = False
        p.warnings = [
            warning
            for warning in p.warnings
            if "phụ đề cũ được giữ nguyên" not in warning.lower()
            and not (p.voice_id and warning.lower().startswith("kịch bản đã đổi"))
        ]
        return store.save(p)


@app.post("/api/projects/{pid}/cues/confirm")
def confirm_cues(pid: str, data: dict):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        p = store.read(pid)
        if data.get("revision") != p.revision:
            raise HTTPException(
                409, "Dự án đã thay đổi. Tải lại trước khi xác nhận phụ đề."
            )
        if not p.cues:
            raise ValueError("Chưa có phụ đề để xác nhận.")
        p.cues_edited = True
        p.cues_stale = False
        p.warnings = [
            warning
            for warning in p.warnings
            if "phụ đề cũ được giữ nguyên" not in warning.lower()
        ]
        return store.save(p)


@app.post("/api/projects/{pid}/undo")
def undo(pid: str):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        return store.undo(pid)


@app.post("/api/projects/{pid}/redo")
def redo(pid: str):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        return store.redo(pid)


@app.get("/api/projects/{pid}/history")
def project_history(pid: str):
    store.read(pid)
    return jobs.history_state(pid)


@app.post("/api/projects/{pid}/assets")
async def upload(
    request: Request,
    pid: str,
    files: list[UploadFile] = File(...),
    role: str = Form("source"),
    auto_assemble: bool = Form(False),
):
    if (
        auto_assemble
        and role == "source"
        and files
        and all(
            Path(file.filename or "").suffix.lower()
            in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
            for file in files
        )
    ):
        role = "voice"
    if role not in ["source", "reference", "voice", "music", "overlay"]:
        raise ValueError("Loại tư liệu không hợp lệ")
    if len(files) > 10:
        raise ValueError("Mỗi lần nhập tối đa 10 file")
    suffixes = [
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v",
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
    ]
    if any(Path(file.filename or "").suffix.lower() not in suffixes for file in files):
        raise ValueError("Định dạng file chưa được hỗ trợ")
    declared_total = sum(file.size or 0 for file in files)
    if declared_total > 5 * 1024**3:
        raise ValueError("Tổng dung lượng mỗi lần nhập tối đa 5 GB")
    stage = None
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        initial = store.read(pid)
        with jobs.project_lock(pid):
            job = jobs.reserve(
                pid, "upload", phase="uploading", message="Đang nhận tư liệu"
            )
    stage = store.ROOT / "staging" / pid / job.id
    job._cleanup = lambda: shutil.rmtree(stage, ignore_errors=True)
    staged = []
    copied = 0
    try:
        (stage / "files").mkdir(parents=True, exist_ok=True)
        (stage / "thumbs").mkdir(parents=True, exist_ok=True)
        for index, file in enumerate(files):
            job.check()
            suffix = Path(file.filename or "").suffix.lower()
            path = stage / "files" / (uid() + suffix)
            size = 0
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    job.check()
                    size += len(chunk)
                    copied += len(chunk)
                    if await request.is_disconnected():
                        raise jobs.Cancelled("Đã hủy tải lên")
                    if size > 2 * 1024**3:
                        raise ValueError("Mỗi file tối đa 2 GB")
                    if copied > 5 * 1024**3:
                        raise ValueError("Tổng dung lượng mỗi lần nhập tối đa 5 GB")
                    await anyio.to_thread.run_sync(output.write, chunk)
            staged.append((path, Path(file.filename or "Tư liệu").name))
            job.update(
                min(10, 10 * copied / max(1, declared_total)),
                f"Đã nhận file {index+1}/{len(files)}",
                phase="uploading",
            )

        def process(job):
            assets = []
            for index, (path, name) in enumerate(staged):
                job.update(
                    10 + 70 * index / len(staged),
                    "Kiểm tra và tạo ảnh xem trước: " + name,
                )
                assets.append(
                    media.register(
                        pid, path, name, role, job, thumbnail_dir=stage / "thumbs"
                    )
                )

            def commit():
                moved = []
                project_dir = store.project_dir(pid)
                asset_dir = project_dir / "assets"
                thumb_dir = project_dir / "thumbs"
                asset_dir.mkdir(parents=True, exist_ok=True)
                thumb_dir.mkdir(parents=True, exist_ok=True)
                try:
                    for path, _ in staged:
                        dest = asset_dir / path.name
                        os.replace(path, dest)
                        moved.append(dest)
                    for asset in assets:
                        # Audio has no thumbnail; an empty name would resolve to
                        # the thumbs directory and try to replace the whole folder.
                        if not asset.thumbnail:
                            continue
                        thumb_source = stage / "thumbs" / asset.thumbnail
                        if thumb_source.is_file():
                            thumb_dest = thumb_dir / asset.thumbnail
                            os.replace(thumb_source, thumb_dest)
                            moved.append(thumb_dest)
                    current = store.read(pid)
                    if current.revision != initial.revision:
                        raise ValueError(
                            "Dự án đã thay đổi trong lúc nhập tư liệu. Hãy thử nhập lại."
                        )
                    current.assets.extend(assets)
                    if role == "voice":
                        current.voice_id = assets[-1].id
                        current.cue_timing = "estimated"
                        current.cues = planner.estimated_cues(
                            current.script, assets[-1].duration
                        )
                        current.cues_edited = False
                        current.cues_stale = False
                        current.target_duration = min(
                            180, max(current.target_duration, assets[-1].duration)
                        )
                    if role == "music":
                        current.music_id = assets[-1].id
                    saved = store.save(current)
                    return saved
                except BaseException:
                    for path in moved:
                        path.unlink(missing_ok=True)
                    raise

            job.update(85, "Lưu tư liệu vào dự án")
            with jobs.project_lock(pid):
                saved = job.commit(commit)
            return {
                "project_id": saved.id,
                "revision": saved.revision,
                "auto_assemble": bool(
                    auto_assemble and role in ("voice", "source") and saved.voice_id
                ),
            }

        job.update(10, "Đã nhận file, đang chờ kiểm tra", phase="queued")
        job_dump = jobs.launch(job, process)
        return {"project": initial, "job": job_dump}
    except BaseException as exc:
        job.cancelled.set()
        try:
            with job._state_lock:
                job.status = (
                    "cancelled"
                    if isinstance(
                        exc, (jobs.Cancelled, anyio.get_cancelled_exc_class())
                    )
                    else "error"
                )
                job.phase = job.status
                job.message = (
                    "Đã hủy tải lên" if job.status == "cancelled" else str(exc)[:1200]
                )
                job.finished_at = job.updated_at = store.now()
                jobs._persist(job)
        except OSError:
            # Keep the original upload error even if the disk cannot persist its status.
            pass
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        raise


@app.delete("/api/projects/{pid}/assets/{aid}")
def remove_asset(pid: str, aid: str):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        p = store.read(pid)
        asset = store.get_asset(p, aid, include_deleted=False)
        asset.deleted = True
        p.clips = [clip for clip in p.clips if clip.asset_id != aid]
        for layers in [
            p.template.layers,
            *p.template.slot_layers,
            *(clip.layers for clip in p.clips),
        ]:
            for layer in layers:
                if layer.asset_id == aid:
                    layer.asset_id = ""
        if p.voice_id == aid:
            p.voice_id = ""
        if p.music_id == aid:
            p.music_id = ""
        return store.save(p)


@app.get("/api/projects/{pid}/assets/{aid}/file")
def asset_file(pid: str, aid: str):
    p = store.read(pid)
    a = store.get_asset(p, aid)
    return FileResponse(store.asset_path(pid, a))


@app.get("/api/projects/{pid}/assets/{aid}/waveform")
def asset_waveform(pid: str, aid: str, max_points: int = 2000):
    p = store.read(pid)
    a = store.get_asset(p, aid)
    if a.role not in {"voice", "music"}:
        raise ValueError("Waveform chỉ khả dụng cho giọng đọc hoặc nhạc nền")
    return media.waveform_peaks(pid, a, max_points)


@app.get("/api/projects/{pid}/thumbs/{name}")
def thumb(pid: str, name: str):
    if not re.fullmatch(r"[a-zA-Z0-9_]+\.jpg", name):
        raise ValueError("Tên ảnh không hợp lệ")
    return FileResponse(store.project_dir(pid) / "thumbs" / name)


@app.post("/api/projects/{pid}/analyze")
def analyze(pid: str, action: Action):
    def op(p, job):
        sources = [a for a in p.assets if a.role == "source" and not a.deleted]
        if not sources:
            raise ValueError("Chưa có video/ảnh nguồn")
        for i, a in enumerate(sources):
            job.update(5 + 30 * i / len(sources), "Tách cảnh: " + a.name)
            if not a.scenes:
                media.analyze(pid, a, job)
            else:
                media.refresh_scene_thumbnails(pid, a, job)
        if action.use_ai:
            providers.label_scenes(p, job)
            infer_product(p, job)
        if p.avoid_faces:
            face_filter.clean_pool(p, job)
        return p

    return task(pid, "analyze", op)


@app.post("/api/projects/{pid}/plan")
def plan(pid: str, action: Action):
    return task(pid, "plan", lambda p, j: planner.plan(p, action.use_ai, j))


@app.post("/api/projects/{pid}/voice")
def voice(pid: str):
    return task(pid, "voice", providers.synthesize)


@app.post("/api/projects/{pid}/assemble-audio")
def assemble_audio(pid: str):
    return task(pid, "assemble-audio", audio_assembly.assemble)


@app.post("/api/projects/{pid}/filter-faces")
def filter_faces(pid: str):
    return task(pid, "filter-faces", face_filter.filter_timeline)


@app.post("/api/projects/{pid}/transcribe")
def transcribe(pid: str, action: Action):
    return task(
        pid,
        "transcribe",
        lambda p, j: providers.transcribe(
            p, store.get_asset(p, action.asset_id or p.voice_id), j
        ),
    )


@app.get("/api/templates")
def templates():
    saved = []
    for path in (store.ROOT / "templates").glob("*.json"):
        saved.append(Template.model_validate_json(path.read_text(encoding="utf-8")))
    return [bind_placeholders(template) for template in planner.presets() + saved]


@app.get("/api/sound-effects")
def sound_effect_catalog():
    return sound_effects.catalog()


@app.get("/api/sound-effects/{effect}.wav")
def sound_effect_file(effect: str):
    return FileResponse(sound_effects.effect_path(effect), media_type="audio/wav")


@app.get("/api/projects/{pid}/sound-effects/track.wav")
def sound_effect_track(pid: str):
    return FileResponse(
        sound_effects.track_path(store.read(pid)), media_type="audio/wav"
    )


@app.post("/api/projects/{pid}/sound-effects/auto")
def arrange_sound_effects(pid: str):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        p = store.read(pid)
        return store.save(sound_effects.refresh(p))


@app.get("/api/template-packages")
def template_packages_list():
    return template_packages.list_packages()


@app.patch("/api/template-packages/{template_id}")
def rename_template_package(template_id: str, data: dict):
    return template_packages.rename_package(template_id, data.get("name", ""))


@app.post("/api/template-packages/{template_id}/duplicate")
def duplicate_template_package(template_id: str):
    return template_packages.duplicate_package(template_id)


@app.delete("/api/template-packages/{template_id}")
def delete_template_package(template_id: str):
    return template_packages.delete_package(template_id)


@app.get("/api/templates/{template_id}/thumbnail.svg")
def template_thumbnail(template_id: str, aspect: str = "9:16"):
    return Response(
        template_packages.preview_thumbnail(template_id, aspect),
        media_type="image/svg+xml",
    )


@app.post("/api/projects/{pid}/templates")
def save_project_template(pid: str, t: Template):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        p = store.read(pid)
        return template_packages.save_package(p, t)


@app.post("/api/projects/{pid}/templates/{template_id}/apply")
def apply_project_template(pid: str, template_id: str, data: dict | None = None):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        p = store.read(pid)
        try:
            options = data or {}
            aspect = options.get("aspect")
            if aspect is not None:
                if aspect not in {"9:16", "16:9", "1:1"}:
                    raise ValueError("Tỷ lệ khung không hợp lệ.")
                p.aspect = aspect
            return template_packages.apply_package(
                p, template_id, options.get("asset_map", {})
            )
        except MissingTemplateAssets as exc:
            raise HTTPException(
                409, detail={"message": str(exc), "missing_assets": exc.missing}
            ) from exc


@app.post("/api/templates")
def save_template(t: Template):
    if any(
        layer.kind == "image" and layer.asset_id
        for layer in [*t.layers, *(x for group in t.slot_layers for x in group)]
    ):
        raise ValueError(
            "Template có ảnh cần được lưu từ dự án để đóng gói tài nguyên."
        )
    t.id = uid()
    store.atomic(store.ROOT / "templates" / f"{t.id}.json", t.model_dump())
    return t


@app.post("/api/templates/validate")
def validate_template(t: Template):
    return t


@app.post("/api/projects/{pid}/template")
def make_template(pid: str, action: Action):
    def op(p, job):
        a = store.get_asset(p, action.asset_id)
        if a.role != "reference":
            raise ValueError("Chọn một video mẫu")
        job.update(10, "Phân tích nhịp cắt của video mẫu")
        media.analyze(pid, a, job)
        if action.use_ai:
            p.template = providers.infer_template(p, a, job)
        else:
            from .template_utils import compressed_slot_durations

            p.template = planner.presets()[0].model_copy(deep=True)
            p.template.name = "Nhịp mẫu — " + a.name
            p.template.slot_durations = compressed_slot_durations(a)
            simplified = len(a.scenes) > 100
            p.template.notes = (
                (
                    "Đã gộp nhịp liên tiếp để vừa giới hạn 100 ô; toàn bộ cảnh gốc và thời lượng video mẫu vẫn được giữ. "
                    if simplified
                    else ""
                )
                + "Đã lấy nhịp cắt. Chỉnh khung/lớp thủ công hoặc bật AI để nhận diện bố cục."
            )
        p.mode = "template"
        return p

    return task(pid, "template", op)


@app.post("/api/projects/{pid}/render")
def export(pid: str, action: Action):
    current = store.read(pid)
    report = preflight(current, store.project_dir(pid))
    if not report["ok"]:
        raise HTTPException(400, detail=report)

    def op(p, job):
        info = render.render(p, job, action.preview)
        info["project_revision"] = p.revision + 1
        p.exports.append(info)
        return p

    return task(pid, "render", op)


@app.get("/api/projects/{pid}/exports/{eid}/{name}")
def export_file(pid: str, eid: str, name: str):
    p = store.read(pid)
    if not any(e["id"] == eid for e in p.exports):
        raise HTTPException(404, "Bản xuất chưa hoàn tất")
    if name not in [
        "video.mp4",
        "preview.mp4",
        "captions.srt",
        "captions.ass",
        "project.json",
    ]:
        raise HTTPException(404)
    path = store.project_dir(pid) / "exports" / eid / name
    return FileResponse(
        path, filename=name if name not in ["video.mp4", "preview.mp4"] else None
    )


@app.get("/api/projects/{pid}/download")
def download_project(pid: str):
    return JSONResponse(
        store.read(pid).model_dump(),
        headers={"Content-Disposition": 'attachment; filename="project.json"'},
    )


@app.post("/api/projects/{pid}/import-edit")
def import_edit(pid: str, data: Project):
    old = store.read(pid)
    data.id = pid
    data.revision = old.revision
    return _update_project(pid, data)


@app.get("/api/jobs")
def list_jobs(pid: str | None = None):
    return jobs.all_jobs(pid)


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    j = jobs.cancel(jid)
    if not j:
        raise HTTPException(404, "Không tìm thấy tác vụ")
    return j


def _ai_candidate(project: Project, request: AIApplyRequest) -> Project:
    if request.revision != project.revision:
        raise HTTPException(
            409, "Dự án đã thay đổi từ lúc AI đề xuất. Hãy gửi lại yêu cầu AI."
        )
    clip_ids = [change.id for change in request.clip_changes]
    if len(clip_ids) != len(set(clip_ids)):
        raise ValueError("Đề xuất AI lặp ID cảnh. Hãy tạo lại đề xuất.")
    clips = {clip.id: clip for clip in project.clips}
    unknown = set(clip_ids) - set(clips)
    if unknown:
        raise ValueError("Đề xuất AI chứa ID cảnh không tồn tại. Hãy tạo lại đề xuất.")
    values = project.model_dump()
    if request.script is not None:
        values["script"] = request.script
    if request.music_volume is not None:
        values["music_volume"] = request.music_volume
    changes = {
        change.id: change.model_dump(exclude_none=True)
        for change in request.clip_changes
    }
    for index, clip in enumerate(project.clips):
        change = changes.get(clip.id)
        if not change:
            continue
        if clip.locked:
            raise ValueError(
                f"Không thể áp dụng đề xuất AI cho cảnh đã khóa ({clip.id})."
            )
        change.pop("id", None)
        clip_values = clip.model_dump()
        original_asset = clip_values["asset_id"]
        clip_values.update(change)
        if clip_values["asset_id"] != original_asset:
            clip_values["scene_id"] = ""
        values["clips"][index] = Clip.model_validate(clip_values).model_dump()
    candidate = _normalize_project_update(project, Project.model_validate(values))
    validate_project(candidate)
    return candidate


_AI_PREVIEWS = {}
_AI_PREVIEW_TTL = 900


def _ai_request_fingerprint(pid: str, request: AIApplyRequest) -> str:
    body = json.dumps(
        request.model_dump(exclude_none=True, exclude={"preview_token"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{pid}:{body}".encode("utf-8")).hexdigest()


@app.post("/api/projects/{pid}/assistant")
def assistant(pid: str, action: Action):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        base = store.read(pid)
        if action.revision is None:
            raise HTTPException(422, "Cần revision hiện tại để gửi yêu cầu AI.")
        if action.revision != base.revision:
            raise HTTPException(
                409, "Dự án đã thay đổi. Tải lại trước khi gửi yêu cầu AI."
            )

        def work(job):
            job.update(10, "AI đang đề xuất chỉnh sửa")
            result = providers.assistant(base, action.prompt, job)
            result["revision"] = base.revision
            result["proposal_id"] = job.id
            return result

        return jobs.submit(pid, "assistant", work)


@app.post("/api/projects/{pid}/assistant-preview")
def assistant_preview(pid: str, request: AIApplyRequest):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        project = store.read(pid)
        candidate = _ai_candidate(project, request)
        report = preflight(candidate, store.project_dir(pid))
        token = ""
        if report["ok"]:
            token = secrets.token_urlsafe(32)
            _AI_PREVIEWS[token] = (
                pid,
                request.revision,
                _ai_request_fingerprint(pid, request),
                time.time(),
            )
            expired = [
                key
                for key, value in _AI_PREVIEWS.items()
                if time.time() - value[3] > _AI_PREVIEW_TTL
            ]
            for key in expired:
                _AI_PREVIEWS.pop(key, None)
            while len(_AI_PREVIEWS) > 500:
                oldest = min(_AI_PREVIEWS, key=lambda key: _AI_PREVIEWS[key][3])
                _AI_PREVIEWS.pop(oldest, None)
        return {
            "project": candidate.model_dump(),
            "preflight": report,
            "preview_token": token,
            "can_apply": report["ok"],
        }


@app.post("/api/projects/{pid}/apply-ai")
def apply_ai(pid: str, request: AIApplyCommit):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        project = store.read(pid)
        fingerprint = _ai_request_fingerprint(pid, request)
        saved = _AI_PREVIEWS.pop(request.preview_token, None)
        if (
            not saved
            or saved[:3] != (pid, request.revision, fingerprint)
            or time.time() - saved[3] > _AI_PREVIEW_TTL
        ):
            raise HTTPException(
                409, "Cần xem trước và kiểm tra lại đề xuất AI trước khi áp dụng."
            )
        candidate = _ai_candidate(project, request)
        report = preflight(candidate, store.project_dir(pid))
        if not report["ok"]:
            raise HTTPException(400, detail=report)
        return _update_project(pid, candidate)


@app.post("/api/demo")
def create_demo():
    from .demo import build_demo

    p = store.save(
        Project(
            name="Một chút bình yên",
            script="Đôi khi, điều bạn cần chỉ là một chuyến đi ngắn. Thức dậy giữa những ngọn đồi xanh. Lắng nghe tiếng sóng và để những lo âu trôi xa. Đi chậm qua một con phố mới. Ngắm bầu trời đổi màu khi chiều xuống. Hãy dành cho mình một khoảng lặng. Chuyến đi tiếp theo đang chờ bạn.",
            template=planner.presets()[0],
        )
    )

    def op(job):
        fresh = build_demo(store.read(p.id), job)
        job.check()
        job.commit(lambda: store.save(fresh))
        return {"project_id": p.id}

    return {"project": p, "job": jobs.submit(p.id, "demo", op)}


FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
