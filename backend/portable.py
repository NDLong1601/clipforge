"""Portable project archives and local storage maintenance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from . import jobs, store
from .models import Project, uid
from .project_validation import validate_project, validate_project_files

ARCHIVE_FORMAT = "clipforge-project"
ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 10 * 1024**3
MAX_UNPACKED_BYTES = 10 * 1024**3
MAX_FILE_BYTES = 5 * 1024**3
MAX_ARCHIVE_FILES = 20_000
MAX_MANIFEST_BYTES = 8 * 1024**2
MAX_PROJECT_JSON_BYTES = 64 * 1024**2
CHUNK_SIZE = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_relative(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("Archive chứa đường dẫn không hợp lệ.")
    path = PurePosixPath(name)
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise ValueError("Archive chứa đường dẫn thoát khỏi thư mục dự án.")
    return path


def _file_record(path: str, kind: str, **fields):
    return {"path": path, "kind": kind, **fields}


def create_backup(
    pid: str, include_exports: bool = False, include_history: bool = False
) -> Path:
    """Create a consistent, streamed ZIP snapshot and return its temporary path."""
    with jobs.maintenance(pid):
        project = store.read(pid)
        project_root = store.project_dir(pid)
        validate_project_files(project, project_root)
        snapshot = json.dumps(
            project.model_dump(), ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8")
        sources: list[tuple[Path | None, bytes | None, dict]] = [
            (None, snapshot, _file_record("project.json", "project"))
        ]
        assets_by_id = {asset.id: asset for asset in project.assets}
        histories = []
        history_export_ids = set()
        if include_history:
            for folder in ("history", "redo"):
                history_root = project_root / folder
                if not history_root.is_dir():
                    continue
                for path in sorted(history_root.glob("*.json")):
                    if not path.is_file() or path.is_symlink():
                        continue
                    try:
                        historical = Project.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                        validate_project_files(historical, project_root)
                    except (OSError, ValueError, ValidationError) as exc:
                        raise ValueError(
                            f"Lịch sử {path.name} không hợp lệ hoặc thiếu media."
                        ) from exc
                    histories.append((folder, path))
                    history_export_ids.update(
                        str(item.get("id", ""))
                        for item in historical.exports
                        if isinstance(item, dict)
                    )
                    for asset in historical.assets:
                        assets_by_id.setdefault(asset.id, asset)
        for asset in assets_by_id.values():
            path = store.asset_path(pid, asset)
            if path.is_file():
                suffix = path.suffix.lower()
                sources.append(
                    (
                        path,
                        None,
                        _file_record(
                            f"assets/{asset.id}{suffix}", "asset", asset_id=asset.id
                        ),
                    )
                )
        thumbs = project_root / "thumbs"
        if thumbs.is_dir():
            for path in sorted(thumbs.iterdir()):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.name not in {".", ".."}
                ):
                    sources.append(
                        (path, None, _file_record(f"thumbs/{path.name}", "thumbnail"))
                    )
        if include_exports:
            exports_root = project_root / "exports"
            if exports_root.is_dir():
                allowed_exports = {
                    str(item.get("id", ""))
                    for item in project.exports
                    if isinstance(item, dict)
                }
                allowed_exports.update(history_export_ids)
                for export_id in sorted(allowed_exports):
                    if not re.fullmatch(r"[a-f0-9]{16}", export_id):
                        continue
                    directory = exports_root / export_id
                    if directory.is_dir():
                        for path in sorted(directory.rglob("*")):
                            if (
                                path.is_file()
                                and not path.is_symlink()
                                and _inside(directory, path)
                            ):
                                relative = path.relative_to(directory).as_posix()
                                safe = _safe_relative(relative)
                                sources.append(
                                    (
                                        path,
                                        None,
                                        _file_record(
                                            f"exports/{export_id}/{safe.as_posix()}",
                                            "export",
                                        ),
                                    )
                                )
        if include_history:
            for folder, path in histories:
                sources.append(
                    (path, None, _file_record(f"{folder}/{path.name}", "history"))
                )
        if len(sources) > MAX_ARCHIVE_FILES:
            raise ValueError("Dự án có quá nhiều file để đưa vào một backup.")

        download_root = store.ROOT / "backup_downloads"
        download_root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix=f"{pid}-backup-", suffix=".zip", dir=download_root, delete=False
        )
        archive = Path(handle.name)
        handle.close()
        records = []
        total = 0
        try:
            with zipfile.ZipFile(archive, "w", allowZip64=True) as zf:
                for source, payload, record in sources:
                    name = record["path"]
                    info = zipfile.ZipInfo(name)
                    info.compress_type = (
                        zipfile.ZIP_DEFLATED
                        if name.endswith(".json")
                        else zipfile.ZIP_STORED
                    )
                    digest = hashlib.sha256()
                    size = 0
                    with zf.open(info, "w", force_zip64=True) as target:
                        if payload is not None:
                            chunks = (
                                payload[i : i + CHUNK_SIZE]
                                for i in range(0, len(payload), CHUNK_SIZE)
                            )
                        else:
                            target_path = source.resolve(strict=True)
                            if not _inside(project_root, target_path):
                                raise ValueError(
                                    "File dự án nằm ngoài thư mục an toàn."
                                )
                            file_handle = target_path.open("rb")
                            chunks = iter(lambda: file_handle.read(CHUNK_SIZE), b"")
                        try:
                            for chunk in chunks:
                                size += len(chunk)
                                total += len(chunk)
                                if size > MAX_FILE_BYTES or total > MAX_UNPACKED_BYTES:
                                    raise ValueError(
                                        "Backup vượt giới hạn dung lượng an toàn."
                                    )
                                digest.update(chunk)
                                target.write(chunk)
                        finally:
                            if payload is None:
                                file_handle.close()
                    records.append(
                        {**record, "size": size, "sha256": digest.hexdigest()}
                    )
                manifest = {
                    "format": ARCHIVE_FORMAT,
                    "archive_version": ARCHIVE_VERSION,
                    "schema_version": project.schema_version,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project": {"name": project.name, "revision": project.revision},
                    "options": {"exports": include_exports, "history": include_history},
                    "files": records,
                }
                zf.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                    compress_type=zipfile.ZIP_DEFLATED,
                )
            if archive.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("File backup vượt giới hạn dung lượng tải lên.")
            return archive
        except BaseException:
            archive.unlink(missing_ok=True)
            raise


def _read_archive_manifest(zf: zipfile.ZipFile):
    names = zf.namelist()
    if len(names) > MAX_ARCHIVE_FILES + 1 or len(names) != len(set(names)):
        raise ValueError("Archive có quá nhiều file hoặc tên file bị trùng.")
    try:
        info = zf.getinfo("manifest.json")
    except KeyError as exc:
        raise ValueError("ZIP không có manifest ClipForge.") from exc
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest ZIP vượt giới hạn dung lượng.")
    try:
        manifest = json.loads(zf.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError("Manifest ZIP bị hỏng.") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != ARCHIVE_FORMAT
        or manifest.get("archive_version") != ARCHIVE_VERSION
    ):
        raise ValueError("Đây không phải backup ClipForge được hỗ trợ.")
    records = manifest.get("files")
    if not isinstance(records, list) or not records or len(records) > MAX_ARCHIVE_FILES:
        raise ValueError("Manifest không có danh sách file hợp lệ.")
    expected_names = {"manifest.json"}
    payload_names = []
    total = 0
    seen_assets = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Manifest ZIP có bản ghi file không hợp lệ.")
        path = _safe_relative(record.get("path"))
        name = path.as_posix()
        if name in expected_names:
            raise ValueError("Manifest ZIP chứa đường dẫn trùng.")
        expected_names.add(name)
        payload_names.append(name)
        try:
            size = int(record["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Manifest ZIP thiếu dung lượng file hợp lệ.") from exc
        if size < 0 or size > MAX_FILE_BYTES:
            raise ValueError("Một file trong ZIP vượt giới hạn dung lượng.")
        total += size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError("Tổng dung lượng giải nén vượt giới hạn an toàn.")
        if not re.fullmatch(r"[a-f0-9]{64}", str(record.get("sha256", ""))):
            raise ValueError("Manifest ZIP có hash không hợp lệ.")
        if name == "project.json" and record.get("kind") == "project":
            project_record = record
        elif path.parts[0] == "assets":
            if len(path.parts) != 2 or record.get("kind") != "asset":
                raise ValueError("Archive chứa đường dẫn asset không hợp lệ.")
            asset_id = record.get("asset_id")
            if (
                not isinstance(asset_id, str)
                or not re.fullmatch(r"[a-f0-9]{16}", asset_id)
                or asset_id in seen_assets
            ):
                raise ValueError("Manifest ZIP chứa ID asset không hợp lệ hoặc trùng.")
            if Path(path.parts[1]).stem != asset_id:
                raise ValueError("Tên file asset không khớp ID trong manifest.")
            seen_assets.add(asset_id)
        elif path.parts[0] == "thumbs":
            if len(path.parts) != 2 or record.get("kind") != "thumbnail":
                raise ValueError("Archive chứa đường dẫn thumbnail không hợp lệ.")
        elif path.parts[0] == "exports":
            if (
                len(path.parts) < 3
                or record.get("kind") != "export"
                or not re.fullmatch(r"[a-f0-9]{16}", path.parts[1])
            ):
                raise ValueError("Archive chứa đường dẫn bản xuất không hợp lệ.")
        elif path.parts[0] in {"history", "redo"}:
            if (
                len(path.parts) != 2
                or not path.parts[1].endswith(".json")
                or record.get("kind") != "history"
            ):
                raise ValueError("Archive chứa đường dẫn lịch sử không hợp lệ.")
        elif name != "project.json" or record.get("kind") != "project":
            raise ValueError("Archive chứa loại file không được hỗ trợ.")
    if "project_record" not in locals():
        raise ValueError("Manifest ZIP thiếu project.json.")
    for name in payload_names:
        if any(other.startswith(name + "/") for other in payload_names):
            raise ValueError("Archive có đường dẫn file lồng nhau không hợp lệ.")
    if set(names) != expected_names:
        raise ValueError(
            "Archive chứa file ngoài manifest hoặc thiếu file đã khai báo."
        )
    for info in zf.infolist():
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.is_dir():
            raise ValueError("ZIP không được chứa liên kết hoặc thư mục đặc biệt.")
        if info.file_size > MAX_FILE_BYTES:
            raise ValueError("Một file ZIP vượt giới hạn dung lượng.")
    return manifest, project_record


def _extract_verified(zf: zipfile.ZipFile, record: dict, stage: Path) -> Path:
    relative = _safe_relative(record["path"])
    destination = stage.joinpath(*relative.parts)
    if not _inside(stage, destination):
        raise ValueError("Đường dẫn file thoát khỏi khu staging.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with zf.open(record["path"]) as source, destination.open("wb") as target:
        while chunk := source.read(CHUNK_SIZE):
            size += len(chunk)
            if size > int(record["size"]) or size > MAX_FILE_BYTES:
                raise ValueError("Kích thước giải nén không khớp manifest.")
            digest.update(chunk)
            target.write(chunk)
    if size != int(record["size"]) or digest.hexdigest() != record["sha256"]:
        raise ValueError(f'Hash hoặc kích thước không khớp cho {record["path"]}.')
    return destination


def _remap_project(
    project: Project,
    asset_ids: dict[str, str],
    scene_ids: dict[str, str],
    filenames: dict[str, str],
    new_project_id: str | None = None,
    reset_revision: bool = True,
):
    project.id = new_project_id or uid()
    if reset_revision:
        project.revision = 0
    project.updated_at = store.now()
    project.exports = list(project.exports)
    for asset in project.assets:
        old_asset_id = asset.id
        asset.id = asset_ids[old_asset_id]
        asset.filename = filenames.get(old_asset_id, asset.filename)
        for scene in asset.scenes:
            scene.id = scene_ids.get(scene.id, uid())
    project.voice_id = asset_ids.get(project.voice_id, "") if project.voice_id else ""
    project.product_info.source_id = asset_ids.get(project.product_info.source_id, "")
    project.music_id = asset_ids.get(project.music_id, "") if project.music_id else ""
    for clip in project.clips:
        clip.id = uid()
        clip.asset_id = asset_ids[clip.asset_id]
        if clip.scene_id:
            clip.scene_id = scene_ids[clip.scene_id]
        for layer in clip.layers:
            layer.id = uid()
            if layer.asset_id:
                layer.asset_id = asset_ids[layer.asset_id]
    project.template.id = uid()
    for layers in [project.template.layers, *project.template.slot_layers]:
        for layer in layers:
            layer.id = uid()
            if layer.asset_id:
                layer.asset_id = asset_ids[layer.asset_id]
    return project


def _validate_thumbnail_names(project: Project):
    for asset in project.assets:
        names = [asset.thumbnail, *(scene.thumbnail for scene in asset.scenes)]
        for name in names:
            if name and (
                Path(name).name != name
                or name in {".", ".."}
                or "\\" in name
                or "\x00" in name
            ):
                raise ValueError(f"Tên thumbnail không hợp lệ: {name!r}.")


def _project_from_json(payload: str, declared_schema: int | None = None) -> Project:
    try:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("project.json phải là JSON object.")
        if (
            declared_schema is not None
            and raw.get("schema_version", 0) != declared_schema
        ):
            raise ValueError("Schema trong manifest không khớp project.json.")
        return Project.model_validate(store._migrate_project(raw))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as exc:
        raise ValueError("project.json trong ZIP không hợp lệ.") from exc


def import_backup(archive_path: Path) -> Project:
    """Verify, stage, remap and atomically publish a project ZIP."""
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("File backup vượt giới hạn dung lượng tải lên.")
    staging_root = store.ROOT / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="import-", dir=staging_root))
    publish = None
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest, project_record = _read_archive_manifest(zf)
            if project_record["size"] > MAX_PROJECT_JSON_BYTES:
                raise ValueError("project.json vượt giới hạn dung lượng.")
            extracted = {}
            for record in manifest["files"]:
                extracted[record["path"]] = _extract_verified(zf, record, staging)
            try:
                project = _project_from_json(
                    extracted["project.json"].read_text(encoding="utf-8"),
                    manifest.get("schema_version"),
                )
            except (
                KeyError,
                OSError,
                UnicodeDecodeError,
                ValidationError,
                ValueError,
            ) as exc:
                raise ValueError("project.json trong ZIP không hợp lệ.") from exc
        validate_project(project)
        _validate_thumbnail_names(project)
        archive_assets = {
            str(record.get("asset_id")): (record, extracted[record["path"]])
            for record in manifest["files"]
            if record.get("kind") == "asset"
        }
        for asset in project.assets:
            if not asset.deleted and asset.id not in archive_assets:
                raise ValueError(f"Backup thiếu file tư liệu đang dùng: {asset.name}.")
        new_id = uid()
        project_dir = store.ROOT / "projects" / new_id
        if project_dir.exists():
            raise ValueError("Không tạo được ID dự án mới; hãy thử lại.")
        history_projects = []
        history_records = []
        for record in manifest["files"]:
            if record.get("kind") == "history":
                try:
                    historical = _project_from_json(
                        extracted[record["path"]].read_text(encoding="utf-8")
                    )
                except (
                    OSError,
                    UnicodeDecodeError,
                    ValidationError,
                    ValueError,
                ) as exc:
                    raise ValueError("Lịch sử trong backup không hợp lệ.") from exc
                validate_project(historical)
                if historical.id != project.id:
                    raise ValueError("Snapshot lịch sử không thuộc dự án trong backup.")
                _validate_thumbnail_names(historical)
                if any(
                    not asset.deleted and asset.id not in archive_assets
                    for asset in historical.assets
                ):
                    raise ValueError(
                        "Backup thiếu media mà một snapshot lịch sử đang dùng."
                    )
                history_projects.append(historical)
                history_records.append(record)
        all_assets = {asset.id: asset for asset in project.assets}
        all_scenes = {
            scene.id: scene for asset in project.assets for scene in asset.scenes
        }
        for historical in history_projects:
            for asset in historical.assets:
                all_assets.setdefault(asset.id, asset)
                for scene in asset.scenes:
                    all_scenes.setdefault(scene.id, scene)
        asset_ids = {asset_id: uid() for asset_id in all_assets}
        scene_ids = {scene_id: uid() for scene_id in all_scenes}
        filenames = {}
        for asset in all_assets.values():
            entry = archive_assets.get(asset.id)
            if entry:
                suffix = Path(entry[0]["path"]).suffix.lower()
                filenames[asset.id] = f"{uid()}{suffix}"
            else:
                filenames[asset.id] = f"{uid()}{Path(asset.filename).suffix.lower()}"
        imported = _remap_project(project, asset_ids, scene_ids, filenames, new_id)
        validate_project(imported)
        publish = staging / "publish"
        (publish / "assets").mkdir(parents=True)
        (publish / "thumbs").mkdir(parents=True)
        for old_asset_id, (record, source) in archive_assets.items():
            if old_asset_id not in filenames:
                continue
            shutil.copyfile(source, publish / "assets" / filenames[old_asset_id])
        for record in manifest["files"]:
            if record.get("kind") == "thumbnail":
                source_name = Path(record["path"]).name
                shutil.copyfile(
                    extracted[record["path"]], publish / "thumbs" / source_name
                )
            elif record.get("kind") == "export":
                target = publish / record["path"]
                if not _inside(publish, target):
                    raise ValueError("Đường dẫn bản xuất không hợp lệ.")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(extracted[record["path"]], target)
            elif record.get("kind") == "history":
                target = publish / record["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(extracted[record["path"]], target)
        included_exports = {
            PurePosixPath(record["path"]).parts[1]
            for record in manifest["files"]
            if record.get("kind") == "export"
        }
        imported.exports = [
            item
            for item in imported.exports
            if isinstance(item, dict) and str(item.get("id", "")) in included_exports
        ]
        # Re-key undo snapshots consistently so restored clips continue to point at imported media.
        for record, historical in zip(history_records, history_projects, strict=True):
            _remap_project(
                historical,
                asset_ids,
                scene_ids,
                filenames,
                new_id,
                reset_revision=False,
            )
            historical.exports = [
                item
                for item in historical.exports
                if isinstance(item, dict)
                and str(item.get("id", "")) in included_exports
            ]
            validate_project(historical)
            history_target = publish / record["path"]
            history_target.parent.mkdir(parents=True, exist_ok=True)
            history_target.write_text(
                json.dumps(historical.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        store.atomic(publish / "project.json", imported.model_dump())
        validate_project_files(imported, publish)
        with jobs.LOCK, store.LOCK:
            if project_dir.exists():
                raise ValueError("ID dự án mới vừa bị sử dụng; hãy thử nhập lại.")
            project_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(publish, project_dir)
        return imported
    except zipfile.BadZipFile as exc:
        raise ValueError("File ZIP bị hỏng hoặc không đọc được.") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _bytes(path: Path) -> int:
    total = 0
    if path.is_file() and not path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.is_dir() or path.is_symlink():
        return 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            file_path = Path(root) / name
            if file_path.is_symlink():
                continue
            try:
                total += file_path.stat().st_size
            except OSError:
                pass
    return total


def storage_report() -> dict:
    projects_root = store.ROOT / "projects"
    cache_root = store.ROOT / "cache"
    template_root = store.ROOT / "templates"
    trash_root = store.ROOT / "trash"
    category_totals = {
        "media": 0,
        "cache": _bytes(cache_root),
        "exports": 0,
        "history": 0,
        "templates": _bytes(template_root),
        "trash": _bytes(trash_root),
        "other": 0,
    }
    project_rows = []
    if projects_root.is_dir():
        for directory in projects_root.iterdir():
            if not directory.is_dir() or not re.fullmatch(
                r"[a-f0-9]{16}", directory.name
            ):
                continue
            sizes = {
                "media": _bytes(directory / "assets"),
                "cache": _bytes(directory / "cache"),
                "exports": _bytes(directory / "exports"),
                "history": _bytes(directory / "history")
                + _bytes(directory / "redo")
                + _bytes(directory / "backups"),
            }
            sizes["other"] = max(0, _bytes(directory) - sum(sizes.values()))
            for key, size in sizes.items():
                category_totals[key] = category_totals.get(key, 0) + size
            try:
                project = store.read(directory.name)
                name = project.name
            except (OSError, ValueError, ValidationError, json.JSONDecodeError):
                name = "Dự án cần phục hồi"
            project_rows.append(
                {
                    "id": directory.name,
                    "name": name,
                    **sizes,
                    "total": sum(sizes.values()),
                }
            )
    for name in (
        "settings.json",
        "jobs",
        "staging",
        "backup_downloads",
        "instance.lock",
    ):
        category_totals["other"] += _bytes(store.ROOT / name)
    total = sum(category_totals.values())
    return {
        "root": str(store.ROOT),
        "categories": category_totals,
        "total": total,
        "projects": sorted(project_rows, key=lambda row: row["total"], reverse=True),
        "trash": list_trash(),
        "cache_cleanup_bytes": _bytes(cache_root),
    }


def cleanup_preview() -> dict:
    root = store.ROOT / "cache"
    return {
        "bytes_to_free": _bytes(root),
        "files": (
            sum(
                1
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            if root.is_dir() and not root.is_symlink()
            else 0
        ),
        "scope": "cache",
    }


def cleanup_cache() -> dict:
    with jobs.LOCK:
        if any(
            job.status in {"queued", "running", "cancelling"}
            for job in jobs.JOBS.values()
        ):
            raise ValueError("Đang có tác vụ chạy. Hãy đợi xong trước khi dọn cache.")
        root = store.ROOT / "cache"
        preview = cleanup_preview()
        if root.is_dir() and not root.is_symlink():
            for child in root.iterdir():
                if child.is_symlink() or child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    shutil.rmtree(child)
        return {**preview, "deleted_bytes": preview["bytes_to_free"]}


def trash_project(pid: str) -> dict:
    if jobs.busy(pid):
        raise ValueError(
            "Dự án đang chạy tác vụ. Hãy đợi trước khi chuyển vào thùng rác."
        )
    with jobs.LOCK, store.LOCK, jobs.project_lock(pid):
        if jobs.busy(pid):
            raise ValueError(
                "Dự án đang chạy tác vụ. Hãy đợi trước khi chuyển vào thùng rác."
            )
        source = store.project_dir(pid)
        project = store.read(pid)
        trash_root = store.ROOT / "trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        trash_id = f"{pid}-{stamp}"
        target = trash_root / trash_id
        os.replace(source, target)
        try:
            store.atomic(
                target / ".clipforge-trash.json",
                {
                    "id": trash_id,
                    "project_id": pid,
                    "name": project.name,
                    "trashed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except BaseException:
            os.replace(target, source)
            raise
        return {
            "id": trash_id,
            "project_id": pid,
            "name": project.name,
            "trashed_at": datetime.now(timezone.utc).isoformat(),
            "bytes": _bytes(target),
        }


def list_trash() -> list[dict]:
    root = store.ROOT / "trash"
    result = []
    if root.is_dir():
        for directory in root.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                item = json.loads(
                    (directory / ".clipforge-trash.json").read_text(encoding="utf-8")
                )
                if item.get("id") != directory.name or not re.fullmatch(
                    r"[a-f0-9]{16}-\d{8}T\d{12}Z", directory.name
                ):
                    continue
                result.append({**item, "bytes": _bytes(directory)})
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return sorted(result, key=lambda item: item["trashed_at"], reverse=True)


def _trash_path(trash_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{16}-\d{8}T\d{12}Z", trash_id):
        raise ValueError("ID trong thùng rác không hợp lệ.")
    root = (store.ROOT / "trash").resolve()
    path = (root / trash_id).resolve()
    if path.parent != root or not path.is_dir() or path.is_symlink():
        raise FileNotFoundError("Không tìm thấy dự án trong thùng rác.")
    try:
        item = json.loads((path / ".clipforge-trash.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Thông tin dự án trong thùng rác không hợp lệ.") from exc
    if item.get("id") != trash_id or item.get("project_id") != trash_id[:16]:
        raise ValueError("Thông tin dự án trong thùng rác không khớp.")
    return path


def restore_project(trash_id: str) -> dict:
    with jobs.LOCK, store.LOCK:
        path = _trash_path(trash_id)
        item = json.loads((path / ".clipforge-trash.json").read_text(encoding="utf-8"))
        destination = store.project_dir(item["project_id"])
        if destination.exists():
            raise ValueError("Đã có dự án cùng ID; không thể phục hồi an toàn.")
        project = store._read_project_file(
            path / "project.json", expected_id=item["project_id"]
        )
        validate_project_files(project, path)
        os.replace(path, destination)
        try:
            (destination / ".clipforge-trash.json").unlink(missing_ok=True)
        except OSError:
            pass
        return {"id": item["project_id"], "name": project.name}


def permanently_delete_trash(trash_id: str) -> dict:
    with jobs.LOCK, store.LOCK:
        path = _trash_path(trash_id)
        item = json.loads((path / ".clipforge-trash.json").read_text(encoding="utf-8"))
        size = _bytes(path)
        shutil.rmtree(path)
        return {
            "id": trash_id,
            "name": item.get("name", "Dự án"),
            "deleted_bytes": size,
        }
