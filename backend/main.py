import json, re, shutil, os, tempfile, secrets, hashlib, time
from types import SimpleNamespace
import anyio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError
from . import (
    store,
    jobs,
    job_store,
    media,
    providers,
    planner,
    render,
    portrait,
    audio_assembly,
    face_filter,
)
from .preflight import preflight
from . import template_packages
from . import portable
from . import diagnostics
from .errors import safe_message
from .template_packages import MissingTemplateAssets
from .models import Project, Asset, Action, Template, Clip, uid, AIApplyRequest, AIApplyCommit, PortraitRequest
from .models import WhiteboardImagesRequest
from .project_validation import validate_project
from .font_manager import available as available_fonts
from . import sound_effects
from .template_content import bind_placeholders, infer_product
from .whiteboard import engine as whiteboard_engine, importer as whiteboard_importer, storyboard

ASSET_ROLES = {"source", "reference", "voice", "music", "overlay", "illustration", "product"}


@asynccontextmanager
async def lifespan(_app):
    from . import processes
    processes.ensure_owner()
    store.acquire_instance_lock()
    try:
        jobs.ROOT = store.ROOT
        jobs.initialize()
        jobs.cleanup_staging()
        yield
    finally:
        if jobs.stop_render_jobs():
            store.release_instance_lock()
        else:
            raise RuntimeError("Worker vẫn đang dừng; giữ khóa instance đến khi tiến trình backend thoát.")


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


def task(pid, kind, operation, revision=None):
    """Submit only serializable operation arguments; handlers live in the registry."""
    payload = operation if isinstance(operation, dict) else {}
    with jobs.LOCK, store.LOCK:
        snapshot = store.read(pid)
        if revision is not None and revision != snapshot.revision:
            raise HTTPException(409, "Dự án đã đổi. Tải lại dự án trước khi tạo tác vụ.")
        return jobs.submit(
            pid,
            kind,
            payload,
            snapshot.model_dump(),
            config_fingerprint=_job_config_fingerprint(kind),
        )


def _job_config_fingerprint(kind):
    from . import job_inputs
    return job_inputs.config_fingerprint(kind)


def _run_project_job(job):
    from . import job_inputs
    if job._settings is None:
        job_inputs.validate_before_run(job, capture_settings=True)
    snapshot = job_store.load_snapshot(jobs.ROOT, job.snapshot_ref)
    p = Project.model_validate(snapshot)
    payload = job.payload
    render_info = None
    result_fingerprint = job_store.fingerprint({
        "input": job.input_fingerprint,
        "config": job.config_fingerprint,
        "kind": job.kind,
        "operation_version": job.operation_version,
        "media": job_inputs.media_identity(p),
        "result_schema": job_inputs.PROJECT_RESULT_VERSION,
    })
    cached_value = (
        job.load_checkpoint("project-result", result_fingerprint)
        if job.kind not in jobs.EXPORT_KINDS
        else None
    )
    cached_project = job_inputs.cached_project(cached_value, p)
    if cached_project is not None:
        p = cached_project
    elif job.kind == "analyze":
        sources = [a for a in p.assets if a.role == "source" and not a.deleted]
        if not sources:
            raise ValueError("Chưa có video/ảnh nguồn")
        for index, source_asset in enumerate(sources):
            asset = store.get_asset(p, source_asset.id)
            source_path = store.asset_path(p.id, asset)
            fingerprint = job_store.fingerprint({
                "asset_id": asset.id,
                "source_sha256": media.file_sha256(source_path),
                "analysis_version": media.ANALYSIS_VERSION,
                "thumbnail_version": 1,
            })
            cached_asset = job_inputs.cached_asset(job.load_checkpoint(f"analyze-asset:{asset.id}", fingerprint), p, asset)
            if cached_asset is not None:
                p.assets[p.assets.index(asset)] = cached_asset
                continue
            job.update(5 + 30 * index / len(sources), "Tách cảnh: " + asset.name)
            if not asset.scenes:
                media.analyze(p.id, asset, job)
            else:
                media.refresh_scene_thumbnails(p.id, asset, job)
            job.save_checkpoint(f"analyze-asset:{asset.id}", fingerprint, job_inputs.asset_result(p, asset))
        if payload.get("use_ai"):
            providers.label_scenes(p, job)
            infer_product(p, job)
        if p.avoid_faces:
            face_filter.clean_pool(p, job)
    elif job.kind == "plan":
        p = planner.plan(p, bool(payload.get("use_ai")), job)
    elif job.kind == "voice":
        p = providers.synthesize(p, job)
    elif job.kind == "assemble-audio":
        p = audio_assembly.assemble(p, job)
    elif job.kind == "filter-faces":
        p = face_filter.filter_timeline(p, job)
    elif job.kind == "transcribe":
        asset = store.get_asset(p, payload.get("asset_id") or p.voice_id)
        p = providers.transcribe(p, asset, job)
    elif job.kind == "template":
        asset = store.get_asset(p, payload.get("asset_id"))
        if asset.role != "reference":
            raise ValueError("Chọn một video mẫu")
        job.update(10, "Phân tích nhịp cắt của video mẫu")
        media.analyze(p.id, asset, job)
        if payload.get("use_ai"):
            p.template = providers.infer_template(p, asset, job)
        else:
            from .template_utils import compressed_slot_durations

            p.template = planner.presets()[0].model_copy(deep=True)
            p.template.name = "Nhịp mẫu — " + asset.name
            p.template.slot_durations = compressed_slot_durations(asset)
            simplified = len(asset.scenes) > 100
            p.template.notes = (
                ("Đã gộp nhịp liên tiếp để vừa giới hạn 100 ô; toàn bộ cảnh gốc và thời lượng video mẫu vẫn được giữ. " if simplified else "")
                + "Đã lấy nhịp cắt. Chỉnh khung/lớp thủ công hoặc bật AI để nhận diện bố cục."
            )
        p.mode = "template"
    elif job.kind == "render":
        render_info = render.render(p, job, bool(payload.get("preview")))
    elif job.kind == "portrait":
        render_info = portrait.render(p, job, payload)
    elif job.kind == "whiteboard-render":
        render_info = whiteboard_engine.render(p, job)
    elif job.kind == "whiteboard-plan":
        p = storyboard.plan(p, bool(payload.get("use_ai")), job)
    elif job.kind == "whiteboard-images":
        p = storyboard.images(p, payload, job)
    elif job.kind == "demo":
        from .demo import build_demo

        p = build_demo(p, job)
    else:
        raise ValueError(f"Không có project handler cho loại job {job.kind}")
    if render_info is None and cached_project is None:
        job.save_checkpoint("project-result", result_fingerprint, job_inputs.project_result(p))
    job.check()
    if render_info is not None:
        def commit_render_export():
            latest = store.read(p.id)
            if any(item.get("id") == job.id for item in latest.exports):
                return latest
            render_info["project_revision"] = latest.revision + 1
            latest.exports.append(render_info)
            return store.save(latest)

        saved = job.commit(commit_render_export, project_id=p.id, allow_latest=True)
    else:
        saved = job.commit(lambda: store.save(p), project_id=p.id)
    return {"project_id": saved.id, "revision": saved.revision}


def _run_assistant_job(job):
    p = Project.model_validate(job_store.load_snapshot(jobs.ROOT, job.snapshot_ref))
    job.update(10, "AI đang đề xuất chỉnh sửa")
    prompt = _ASSISTANT_INPUTS.pop(job.id, "")
    if not prompt:
        raise ValueError("Yêu cầu AI cần được gửi lại sau khi ứng dụng khởi động lại.")
    result = providers.assistant(p, prompt, job)
    result["revision"] = p.revision
    result["proposal_id"] = job.id
    return result


def _run_upload_job(job):
    payload = job.payload
    from . import upload_recovery
    from .recovery_paths import checked_path
    stage = checked_path(store.ROOT, store.ROOT / "staging" / job.pid / job.id)
    previous_moves = {entry["destination"]: entry for entry in upload_recovery.load(store.ROOT, job.pid, job.id)}
    files = payload.get("files", [])
    if not files or payload.get("upload_incomplete"):
        raise ValueError("Upload chưa nhận đủ file; hãy chọn file để gửi lại.")
    role = payload.get("role", "source")
    if role not in ASSET_ROLES:
        raise ValueError("Loại tư liệu không hợp lệ")
    staged = []
    assets = []
    for index, item in enumerate(files):
        staged_name = Path(str(item.get("staged_name", ""))).name
        original_name = Path(str(item.get("name", "Tư liệu"))).name
        path = checked_path(store.ROOT, stage / "files" / staged_name)
        destination = checked_path(store.ROOT, store.project_dir(job.pid) / "assets" / staged_name)
        checked_path(store.ROOT, stage / "thumbs")
        checked_path(store.ROOT, store.project_dir(job.pid) / "thumbs")
        if not staged_name or not path.is_file() and not destination.is_file():
            raise ValueError("File tải lên đã mất; hãy chọn file để gửi lại.")
        if not path.is_file():
            owned = previous_moves.get(f"assets/{staged_name}")
            if not owned or media.file_sha256(destination) != owned["sha256"]:
                raise ValueError("File tại đích upload không còn khớp journal; dữ liệu được giữ để kiểm tra.")
            path = destination
        staged.append((path, original_name))
        job.update(10 + 70 * index / len(files), "Kiểm tra và tạo ảnh xem trước: " + original_name)
        content_hash = media.file_sha256(path)
        fingerprint = job_store.fingerprint({
            "staged_name": staged_name,
            "name": original_name,
            "role": role,
            "sha256": content_hash,
            "version": 1,
        })
        step_id = f"upload-file:{index:03d}"
        cached = job.load_checkpoint(step_id, fingerprint)
        asset = None
        if isinstance(cached, dict):
            try:
                candidate = Asset.model_validate(cached)
                thumb = store.project_dir(job.pid) / "thumbs" / candidate.thumbnail
                stage_thumb = stage / "thumbs" / candidate.thumbnail
                if (candidate.filename == staged_name
                        and media.file_sha256(path) == content_hash
                        and (not candidate.thumbnail or thumb.is_file() or stage_thumb.is_file())):
                    asset = candidate
            except (ValueError, TypeError, OSError):
                asset = None
        if asset is None:
            asset = media.register(
                job.pid, path, original_name, role, job, thumbnail_dir=stage / "thumbs"
            )
            job.save_checkpoint(step_id, fingerprint, asset.model_dump())
        assets.append(asset)

    from . import recovery_paths, upload_recovery
    entries = []
    for path, _ in staged:
        entries.append({"source": f"files/{path.name}", "destination": f"assets/{path.name}",
            "sha256": media.file_sha256(path)})
    for asset in assets:
        if asset.thumbnail:
            thumb_source = checked_path(store.ROOT, stage / "thumbs" / asset.thumbnail)
            thumb_dest = checked_path(store.ROOT, store.project_dir(job.pid) / "thumbs" / asset.thumbnail)
            source = thumb_source if thumb_source.is_file() else thumb_dest
            entries.append({"source": f"thumbs/{asset.thumbnail}", "destination": f"thumbs/{asset.thumbnail}",
                "sha256": media.file_sha256(source)})
    for entry in entries:
        destination = recovery_paths.checked_path(store.ROOT, store.project_dir(job.pid) / entry["destination"])
        if destination.is_file() and media.file_sha256(destination) != entry["sha256"]:
            raise ValueError("Đã có file khác tại đích upload; dữ liệu gốc được giữ lại.")

    def commit():
        # Journal every planned move before the first rename. A crash between
        # rename and save can be recovered from either source or destination.
        upload_recovery.prepare(store.ROOT, job.pid, job.id, entries)
        try:
            with store.LOCK:
                current = store.read(job.pid)
                if current.revision != job.input_revision:
                    raise ValueError("Dự án đã thay đổi trong lúc nhập tư liệu. Hãy thử nhập lại.")
                (store.project_dir(job.pid) / "assets").mkdir(parents=True, exist_ok=True)
                (store.project_dir(job.pid) / "thumbs").mkdir(parents=True, exist_ok=True)
                for entry in entries:
                    source = recovery_paths.checked_path(store.ROOT, stage / entry["source"])
                    destination = recovery_paths.checked_path(store.ROOT, store.project_dir(job.pid) / entry["destination"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if source.is_file() and not destination.exists():
                        os.replace(source, destination)
                current.assets.extend(assets)
                if role == "voice":
                    current.voice_id = assets[-1].id
                    current.cue_timing = "estimated"
                    current.cues = planner.estimated_cues(current.script, assets[-1].duration)
                    current.cues_edited = False
                    current.cues_stale = False
                    current.target_duration = min(180, max(current.target_duration, assets[-1].duration))
                if role == "music":
                    current.music_id = assets[-1].id
                return store.save(current)
        except BaseException:
            # Never unlink moved bytes. Only a proven uncommitted upload may
            # return its unreferenced files to staging; retain on uncertainty.
            if jobs.reconcile_commit(job, finalize=False)["state"] == "uncommitted":
                try:
                    upload_recovery.restore(store.ROOT, job.pid, job.id)
                except (OSError, ValueError):
                    pass
            raise

    job.update(85, "Lưu tư liệu vào dự án")
    snapshot = job_store.load_snapshot(jobs.ROOT, job.snapshot_ref)
    auto_assemble = bool(payload.get("auto_assemble") and role in ("voice", "source")
        and (role == "voice" or snapshot.get("voice_id")))
    with jobs.project_lock(job.pid):
        saved = job.commit(commit, project_id=job.pid, result_metadata={"auto_assemble": auto_assemble})
    return {"project_id": saved.id, "revision": saved.revision, "auto_assemble": auto_assemble}


for _kind in ("analyze", "plan", "voice", "assemble-audio", "filter-faces", "transcribe", "template", "render", "portrait",
              "whiteboard-render", "whiteboard-plan", "whiteboard-images", "demo"):
    jobs.register_handler(_kind, _run_project_job)
jobs.register_handler("assistant", _run_assistant_job)
jobs.register_handler("upload", _run_upload_job)
_ASSISTANT_INPUTS: dict[str, str] = {}


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

    from . import processes
    result = processes.run(
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
    if role not in ASSET_ROLES:
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
                pid,
                "upload",
                phase="uploading",
                message="Đang nhận tư liệu",
                payload={"role": role, "auto_assemble": auto_assemble, "files": []},
                snapshot=initial.model_dump(),
                config_fingerprint=_job_config_fingerprint("upload"),
            )
    stage = store.ROOT / "staging" / pid / job.id
    from .recovery_paths import remove_tree
    job._cleanup = lambda: remove_tree(store.ROOT, stage)
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

        job.payload["files"] = [
            {"staged_name": path.name, "name": name} for path, name in staged
        ]
        job.input_fingerprint = job_store.fingerprint(
            {
                "kind": job.kind,
                "operation_version": job.operation_version,
                "payload": job.payload,
                "snapshot": initial.model_dump(),
                "config_fingerprint": job.config_fingerprint,
            }
        )
        job.save_checkpoint(
            "upload-received",
            job_store.fingerprint({"files": job.payload["files"], "role": role}),
            [
                {"staged_name": path.name, "sha256": media.file_sha256(path), "size": path.stat().st_size}
                for path, _ in staged
            ],
        )
        jobs._persist(job)
        job.update(10, "Đã nhận file, đang chờ kiểm tra", phase="queued")
        job_dump = jobs.launch(job)
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
            if staged and job.status != "cancelled":
                # Retain fully received files if a later file/launch failed.
                # Incomplete transfers cannot silently resume as a subset.
                job.payload["files"] = [{"staged_name": path.name, "name": name} for path, name in staged]
                job.payload["upload_incomplete"] = len(staged) != len(files)
                job.input_fingerprint = job_store.fingerprint({"kind": job.kind,
                    "operation_version": job.operation_version, "payload": job.payload,
                    "snapshot": initial.model_dump(), "config_fingerprint": job.config_fingerprint})
                if job.payload["upload_incomplete"]:
                    job.resume_capability = "restart_only"
                jobs._persist_safely(job)
            else:
                try:
                    remove_tree(store.ROOT, stage)
                except (OSError, ValueError):
                    pass
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
        board = p.whiteboard
        if board.character_reference_id == aid:
            board.character_reference_id = ""
        if board.product_id == aid:
            board.product_id = ""
        for scene in board.scenes:
            if scene.image_id == aid:
                scene.image_id, scene.crop, scene.layout = "", None, None
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
    return task(pid, "analyze", {"use_ai": action.use_ai}, action.revision)


@app.post("/api/projects/{pid}/plan")
def plan(pid: str, action: Action):
    return task(pid, "plan", {"use_ai": action.use_ai}, action.revision)


@app.post("/api/projects/{pid}/voice")
def voice(pid: str):
    return task(pid, "voice", {})


@app.post("/api/projects/{pid}/assemble-audio")
def assemble_audio(pid: str, data: dict | None = None):
    upload_id = (data or {}).get("upload_job_id")
    if upload_id is None:
        return task(pid, "assemble-audio", {})
    # Durable idempotency covers two tabs, a lost POST response and reconnect.
    # The reserved job itself is the receipt, before it becomes dispatchable.
    with jobs.LOCK, store.LOCK:
        upload = jobs.JOBS.get(upload_id) if isinstance(upload_id, str) else None
        if not upload or upload.pid != pid or upload.kind != "upload" or upload.status != "done" or not isinstance(upload.result, dict) or not upload.result.get("auto_assemble"):
            raise HTTPException(409, "Tác vụ nhập tư liệu chưa sẵn sàng để dựng theo audio.")
        for existing in jobs.JOBS.values():
            legacy_match = (
                isinstance(upload.result.get("revision"), int)
                and not isinstance(upload.result.get("revision"), bool)
                and not existing.payload.get("upload_job_id")
                and not existing.parent_job_id
                and existing.input_revision == upload.result.get("revision")
                and existing.created_at >= upload.created_at
            )
            if existing.pid == pid and existing.kind == "assemble-audio" and (existing.payload.get("upload_job_id") == upload_id or existing.parent_job_id == upload_id or legacy_match):
                if legacy_match:
                    # Linking a pre-R4 follow-up does not alter its input payload
                    # or invalidate the saved snapshot/fingerprint.
                    existing.parent_job_id = upload_id
                    jobs._persist(existing)
                if existing.status == "queued" and not existing.dispatch_ready:
                    return jobs.launch(existing)
                return existing.dump()
        return task(pid, "assemble-audio", {"upload_job_id": upload_id})


@app.post("/api/projects/{pid}/filter-faces")
def filter_faces(pid: str):
    return task(pid, "filter-faces", {})


@app.post("/api/projects/{pid}/transcribe")
def transcribe(pid: str, action: Action):
    return task(pid, "transcribe", {"asset_id": action.asset_id}, action.revision)


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
    return task(pid, "template", {"asset_id": action.asset_id, "use_ai": action.use_ai}, action.revision)


@app.post("/api/projects/{pid}/render")
def export(pid: str, action: Action):
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        current = store.read(pid)
        if action.revision is not None and action.revision != current.revision:
            raise HTTPException(409, "Dự án đã đổi. Tải lại dự án trước khi xuất.")
        snapshot = current.model_dump()
    report = preflight(current, store.project_dir(pid))
    if not report["ok"]:
        raise HTTPException(400, detail=report)
    with jobs.LOCK, store.LOCK:
        if not store.project_dir(pid).is_dir():
            raise HTTPException(404, "Không tìm thấy dự án")
        return jobs.submit(
            pid,
            "render",
            {"preview": action.preview},
            snapshot,
            config_fingerprint=_job_config_fingerprint("render"),
        )


@app.post("/api/projects/{pid}/portrait")
def export_portrait(pid: str, data: PortraitRequest):
    """Frame a finished export into the whiteboard portrait layout as a new export."""
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        current = store.read(pid)
        if data.revision is not None and data.revision != current.revision:
            raise HTTPException(409, "Dự án đã đổi. Tải lại dự án trước khi xuất.")
        source = next((item for item in current.exports if item.get("id") == data.export_id), None)
        payload = data.model_dump(exclude={"revision"})
        try:
            portrait.options(payload, source)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        source_dir = store.project_dir(pid) / "exports" / data.export_id
        if not (source_dir / str(source.get("filename", ""))).is_file():
            raise HTTPException(409, "File bản xuất nguồn đã bị xóa. Dựng lại video rồi thử lại.")
        return jobs.submit(
            pid,
            "portrait",
            payload,
            current.model_dump(),
            config_fingerprint=_job_config_fingerprint("portrait"),
        )


@app.post("/api/projects/{pid}/whiteboard/render")
def export_whiteboard(pid: str, action: Action):
    """Draw every hand-drawn scene and export the whiteboard video."""
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        current = store.read(pid)
        if action.revision is not None and action.revision != current.revision:
            raise HTTPException(409, "Dự án đã đổi. Tải lại dự án trước khi xuất.")
        try:
            _, duration = whiteboard_engine._voice(current)
            whiteboard_engine.timeline(current, duration)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return jobs.submit(
            pid,
            "whiteboard-render",
            {},
            current.model_dump(),
            config_fingerprint=_job_config_fingerprint("whiteboard-render"),
        )


@app.post("/api/projects/{pid}/whiteboard/plan")
def plan_whiteboard(pid: str, action: Action):
    return task(pid, "whiteboard-plan", {"use_ai": action.use_ai}, action.revision)


@app.post("/api/projects/{pid}/whiteboard/images")
def whiteboard_images(pid: str, data: WhiteboardImagesRequest):
    return task(pid, "whiteboard-images", data.model_dump(exclude={"revision"}), data.revision)


@app.get("/api/projects/{pid}/whiteboard/scenes/{sid}/preview.png")
def whiteboard_preview(pid: str, sid: str):
    image = whiteboard_engine.preview(store.read(pid), sid)
    buffer = __import__("io").BytesIO()
    image.save(buffer, "PNG")
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{pid}/whiteboard/import")
async def import_whiteboard(pid: str, file: UploadFile = File(...), revision: int = Form(...)):
    """Import a ZIP packaged by the srt-whiteboard-animation-custom kit."""
    with jobs.LOCK, store.LOCK:
        unlocked(pid)
        if store.read(pid).revision != revision:
            raise HTTPException(409, "Dự án đã đổi. Tải lại dự án trước khi nhập.")
    staging = store.ROOT / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(suffix=".zip", dir=staging)
    archive = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > whiteboard_importer.MAX_BYTES:
                    raise HTTPException(413, "ZIP vượt 1,5 GB.")
                stream.write(chunk)

        def build():
            current = store.read(pid)
            try:
                return whiteboard_importer.build(pid, archive, current)
            except (KeyError, TypeError, IndexError) as exc:
                raise ValueError(f"project.json thiếu hoặc sai trường: {exc}") from None
            except __import__("zipfile").BadZipFile:
                raise ValueError("File tải lên không phải ZIP hợp lệ.") from None

        board, assets, changes, warnings, written = await anyio.to_thread.run_sync(build)
        try:
            with jobs.LOCK, store.LOCK:
                unlocked(pid)
                current = store.read(pid)
                if current.revision != revision:
                    raise HTTPException(409, "Dự án đã đổi trong lúc nhập. Hãy thử lại.")
                current.assets.extend(assets)
                for key, value in changes.items():
                    setattr(current, key, value)
                current.whiteboard = board
                current.mode = "whiteboard"
                current.warnings = warnings
                return store.save(current)
        except BaseException:
            whiteboard_importer.discard(written)
            raise
    finally:
        archive.unlink(missing_ok=True)


@app.post("/api/jobs/render-batch")
def batch_render(data: dict):
    entries = data.get("projects") if isinstance(data, dict) else None
    preview = data.get("preview", False) if isinstance(data, dict) else False
    if not isinstance(entries, list) or not entries or len(entries) > 50:
        raise HTTPException(422, "Chọn từ 1 đến 50 dự án để xếp hàng xuất.")
    if not isinstance(preview, bool):
        raise HTTPException(422, "preview phải là giá trị boolean.")
    normalized = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise HTTPException(422, "Mỗi dự án cần có id và revision.")
        pid, revision = entry.get("id"), entry.get("revision")
        if not isinstance(pid, str) or not re.fullmatch(r"[a-f0-9]{16}", pid):
            raise HTTPException(422, "Mã dự án không hợp lệ.")
        if pid in seen:
            continue
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise HTTPException(422, "Cần revision hiện tại cho từng dự án.")
        seen.add(pid)
        normalized.append((pid, revision))

    accepted, rejected = [], []
    for pid, revision in normalized:
        try:
            with jobs.LOCK, store.LOCK:
                project = store.read(pid)
                if project.revision != revision:
                    raise ValueError("Dự án đã đổi. Tải lại danh sách trước khi xếp hàng.")
                snapshot = project.model_dump()
            report = preflight(project, store.project_dir(pid))
            if not report.get("ok"):
                issues = report.get("issues") or []
                detail = "; ".join(
                    str(item.get("message", "")) for item in issues[:3]
                    if isinstance(item, dict) and item.get("message")
                ) or "Dự án chưa qua kiểm tra trước khi xuất."
                raise ValueError(detail)
            with jobs.LOCK, store.LOCK:
                if not store.project_dir(pid).is_dir():
                    raise ValueError("Dự án đã được chuyển đi trong lúc kiểm tra.")
                queued = jobs.submit(
                    pid,
                    "render",
                    {"preview": preview},
                    snapshot,
                    config_fingerprint=_job_config_fingerprint("render"),
                )
            accepted.append(queued)
        except Exception as exc:
            rejected.append({"pid": pid, "message": str(exc)[:600]})
    return {"accepted": accepted, "rejected": rejected}


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
        portrait.OUTPUT,
        portrait.COVER,
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
def list_jobs(pid: str | None = None, limit: int = 100, offset: int = 0):
    return [_public_job_details(record, include_result=True) for record in jobs.all_jobs(pid, limit=limit, offset=offset)]


@app.get("/api/jobs/queue")
def get_job_queue():
    return _public_queue_state(jobs.scheduler_state())


@app.patch("/api/jobs/queue")
def update_job_queue(data: dict):
    if not isinstance(data, dict) or not data or set(data) - {"paused", "low_resource"}:
        raise HTTPException(422, "Cần gửi paused hoặc low_resource để cập nhật hàng đợi.")
    if any(not isinstance(value, bool) for value in data.values()):
        raise HTTPException(422, "paused và low_resource phải là giá trị boolean.")
    return _public_queue_state(jobs.update_scheduler(
        paused=data.get("paused"),
        low_resource=data.get("low_resource"),
    ))


def _public_queue_state(state):
    state = dict(state)
    state.pop("disk_volumes", None)
    state["jobs"] = [_public_job_details(record) for record in state.get("jobs", [])]
    return state


def _public_job_details(job, *, include_result=False):
    """Expose actionable job telemetry without payloads, fingerprints, or profile names."""
    details = job.dump() if hasattr(job, "dump") else dict(job)
    private_fields = ["snapshot_ref", "input_fingerprint", "config_fingerprint", "commit_token"]
    if not include_result:
        private_fields.append("result")
    for private in private_fields:
        details.pop(private, None)
    details["message"] = safe_message(details.get("message", ""))
    raw_steps = details.get("steps") if isinstance(details.get("steps"), list) else []
    details["steps"] = [
        {
            "id": safe_message(step.get("id", ""), 80),
            "status": step.get("status"),
            "completed_at": step.get("completed_at"),
        }
        for step in raw_steps if isinstance(step, dict)
    ][:100]
    raw_intents = details.get("provider_intents") if isinstance(details.get("provider_intents"), list) else []
    details["provider_intents"] = [
        {
            key: intent.get(key)
            for key in ("status", "operation", "started_at", "finished_at", "authorized_at")
            if intent.get(key) is not None
        }
        for intent in raw_intents if isinstance(intent, dict)
    ][-50:]
    raw_calls = details.get("provider_calls") if isinstance(details.get("provider_calls"), list) else []
    details["provider_calls"] = [
        {
            **({"provider": call.get("provider") if isinstance(call.get("provider"), str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,30}", call.get("provider")) else "custom"}),
            **{
                key: call.get(key)
                for key in ("fallback", "attempt", "elapsed_ms", "outcome", "status_code")
                if call.get(key) is not None
            },
        }
        for call in raw_calls if isinstance(call, dict)
    ][-50:]
    def public_error(error):
        if not isinstance(error, dict):
            return None
        visible = {
            key: error.get(key)
            for key in ("code", "category", "severity", "job_id", "step_id", "retryable", "actions", "diagnostic_id", "occurred_at")
            if key in error
        }
        visible["message"] = safe_message(error.get("message", ""))
        if visible.get("step_id"):
            visible["step_id"] = safe_message(visible["step_id"], 80)
        visible["safe_details"] = {
            key: error.get("safe_details", {}).get(key)
            for key in ("exception_type", "http_status")
            if isinstance(error.get("safe_details"), dict) and key in error["safe_details"]
        }
        visible["detail"] = visible["message"]
        return visible
    details["error"] = public_error(details.get("error"))
    raw_error_history = details.get("error_history") if isinstance(details.get("error_history"), list) else []
    details["error_history"] = [
        {
            "attempt": entry.get("attempt") if isinstance(entry.get("attempt"), int) else None,
            "finished_at": entry.get("finished_at") if isinstance(entry.get("finished_at"), str) else None,
            "error": public_error(entry.get("error")),
        }
        for entry in raw_error_history if isinstance(entry, dict)
    ][-50:]
    if isinstance(details.get("blocked_reason"), dict):
        details["blocked_reason"] = {
            "code": details["blocked_reason"].get("code"),
            "message": safe_message(details["blocked_reason"].get("message", "")),
        }
    return details


@app.get("/api/jobs/{jid}/diagnostics/preview")
def preview_job_diagnostics(jid: str):
    with jobs.LOCK:
        if jid not in jobs.JOBS:
            raise HTTPException(404, "Không tìm thấy tác vụ")
    return diagnostics.preview_manifest()


@app.get("/api/jobs/{jid}/diagnostics.zip")
def download_job_diagnostics(jid: str):
    with jobs.LOCK:
        job = jobs.JOBS.get(jid)
        if not job:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        snapshot = SimpleNamespace(**job.dump())
        scheduler = jobs.scheduler_state()
    report = diagnostics.build_zip(snapshot, scheduler)
    filename = f"clipforge-diagnostic-{re.sub(r'[^a-f0-9]', '', jid)[:16] or 'job'}.zip"
    return StreamingResponse(
        report,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    with jobs.LOCK:
        job = jobs.JOBS.get(jid)
        if not job:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        details = _public_job_details(job)
        resumable_kinds = {"render", "analyze", "plan", "template", "filter-faces", "assemble-audio", "voice", "transcribe", "upload"}
        details["can_resume"] = bool(
            job.commit_state == "committed" and job.bookkeeping_pending
            or job.kind in resumable_kinds
            and job.status in ("interrupted", "error")
            and job.resume_capability in ("checkpointed", "snapshot")
        )
        details["can_retry_unknown"] = bool(
            job.kind != "assistant"
            and job.status == "blocked"
            and isinstance(job.blocked_reason, dict)
            and job.blocked_reason.get("code") == "PROVIDER_OUTCOME_UNKNOWN"
        )
        details["can_restart_current"] = _can_restart_current(job)
        render_job = job if job.kind == "render" and not job.bookkeeping_pending else None
    if render_job and render_job.kind == "render":
        details["resume_plan"] = render.resume_plan(render_job)
    return details


def _can_restart_current(job):
    return bool(job.status == "blocked" and not job.resource_lease
        and not (job._future and not job._future.done())
        and not (isinstance(job.result, dict) and job.result.get("replacement_job_id"))
        and job.kind in {"render", "analyze", "plan", "template", "filter-faces", "assemble-audio", "voice", "transcribe"}
        and isinstance(job.blocked_reason, dict)
        and job.blocked_reason.get("code") in {"REVISION_CONFLICT", "CONFIG_CHANGED", "CHECKPOINT_INVALID", "MEDIA_CHANGED"})


@app.post("/api/jobs/{jid}/resume")
def resume_job(jid: str, data: dict):
    expected_state_version = data.get("state_version") if isinstance(data, dict) else None
    if not isinstance(expected_state_version, int) or isinstance(expected_state_version, bool):
        raise HTTPException(422, "Cần state_version hiện tại để tiếp tục tác vụ.")
    with jobs.LOCK:
        existing = jobs.JOBS.get(jid)
        if not existing:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        kind = existing.kind
    try:
        resumed = jobs.resume_render(
            jid,
            expected_state_version,
            _job_config_fingerprint(kind),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if resumed is None:
        raise HTTPException(404, "Không tìm thấy tác vụ")
    return resumed


@app.post("/api/jobs/{jid}/restart-current")
def restart_current_job(jid: str, data: dict):
    version = data.get("state_version") if isinstance(data, dict) else None
    if not isinstance(version, int) or isinstance(version, bool):
        raise HTTPException(422, "Cần state_version hiện tại để tạo tác vụ.")
    with jobs.LOCK:
        original = jobs.JOBS.get(jid)
    if original is None:
        raise HTTPException(404, "Không tìm thấy tác vụ")
    with original._commit_lock:
        if jobs.reconcile_commit(original)["state"] != "uncommitted":
            raise HTTPException(409, "Cần hoàn tất xác minh commit trước khi tạo tác vụ khác.")
        with original._state_lock:
            if original.state_version != version or not _can_restart_current(original):
                raise HTTPException(409, "Tác vụ đã đổi hoặc không hỗ trợ tạo lại từ bản hiện tại.")
            if original._future and not original._future.done():
                raise HTTPException(409, "Worker trước chưa dừng.")
        payload = original.payload.copy()
        with store.LOCK:
            current = store.read(original.pid)
        if original.kind == "render":
            report = preflight(current, store.project_dir(current.id))
            if not report["ok"]:
                raise HTTPException(400, detail=report)
        config = _job_config_fingerprint(original.kind)
        with jobs.LOCK, store.LOCK:
            if jobs.busy(current.id):
                raise HTTPException(409, "Dự án đang có tác vụ khác. Chờ hoàn tất trước khi tạo tác vụ.")
            replacement = jobs.reserve(current.id, original.kind, payload=payload,
                snapshot=current.model_dump(), config_fingerprint=config)
            replacement.parent_job_id = original.id
            jobs._persist(replacement)
            with original._state_lock:
                original.result = {"replacement_job_id": replacement.id}
                original.message = "Đã tạo tác vụ từ bản hiện tại; checkpoint của tác vụ cũ được giữ lại."
                original.updated_at = store.now()
                original.state_version += 1
                jobs._persist(original)
            return jobs.launch(replacement)


@app.post("/api/jobs/{jid}/retry-unknown")
def retry_unknown_job(jid: str, data: dict):
    if not isinstance(data, dict) or data.get("confirm_cost") is not True:
        raise HTTPException(422, "Cần xác nhận rằng gửi lại có thể phát sinh phí thêm.")
    expected_state_version = data.get("state_version")
    if not isinstance(expected_state_version, int) or isinstance(expected_state_version, bool):
        raise HTTPException(422, "Cần state_version hiện tại để xác nhận.")
    with jobs.LOCK:
        existing = jobs.JOBS.get(jid)
        if not existing:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        kind = existing.kind
    try:
        authorized = jobs.authorize_unknown_retry(jid, expected_state_version)
        if authorized is None:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        return jobs.resume_render(jid, authorized["state_version"], _job_config_fingerprint(kind))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    j = jobs.cancel(jid)
    if not j:
        raise HTTPException(404, "Không tìm thấy tác vụ")
    return j


@app.post("/api/jobs/{jid}/promote")
def promote_job(jid: str):
    try:
        promoted = jobs.promote(jid)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not promoted:
        raise HTTPException(404, "Không tìm thấy tác vụ")
    return promoted


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

        job = jobs.reserve(
            pid,
            "assistant",
            payload={"prompt_fingerprint": job_store.fingerprint(action.prompt)},
            snapshot=base.model_dump(),
            config_fingerprint=_job_config_fingerprint("assistant"),
        )
        _ASSISTANT_INPUTS[job.id] = action.prompt
        return jobs.launch(job)


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
    p = store.save(
        Project(
            name="Một chút bình yên",
            script="Đôi khi, điều bạn cần chỉ là một chuyến đi ngắn. Thức dậy giữa những ngọn đồi xanh. Lắng nghe tiếng sóng và để những lo âu trôi xa. Đi chậm qua một con phố mới. Ngắm bầu trời đổi màu khi chiều xuống. Hãy dành cho mình một khoảng lặng. Chuyến đi tiếp theo đang chờ bạn.",
            template=planner.presets()[0],
        )
    )

    return {"project": p, "job": jobs.submit(p.id, "demo", {}, p.model_dump())}


FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
