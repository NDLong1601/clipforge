"""Import a project made with the srt-whiteboard-animation-custom kit (ZIP).

Keeps the voice, music, product photo, locked character reference, scene
images and crops, hand-placed image boxes, drawing regions with their reveal
times and the keyword titles. Kit captions, effects and the presentation
section are reported as skipped.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import PurePosixPath

from .. import media, store
from ..models import Cue, WBElement, WBLayout, WBRect, WBScene, WBTitle, Whiteboard, uid

MAX_FILES = 400
MAX_BYTES = 1536 * 1024**2
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def _fraction(value, total):
    return max(0.0, min(1.0, float(value) / total))


def _rect(x, y, w, h, width, height):
    x0, y0 = _fraction(x, width), _fraction(y, height)
    return WBRect(x=x0, y=y0, w=max(1e-4, min(1 - x0, _fraction(w, width))),
                  h=max(1e-4, min(1 - y0, _fraction(h, height))))


class _Archive:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        infos = [item for item in self.zip.infolist() if not item.is_dir()]
        if len(infos) > MAX_FILES * 10 or sum(item.file_size for item in infos) > MAX_BYTES * 4:
            raise ValueError("ZIP quá lớn để nhập.")
        names = {}
        for item in infos:
            name = PurePosixPath(item.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts:
                continue
            names[name.as_posix()] = item
        self.names = names
        candidates = sorted((name for name in names if PurePosixPath(name).name == "project.json"
                             and "toolkit" not in PurePosixPath(name).parts), key=lambda name: name.count("/"))
        if not candidates:
            raise ValueError("ZIP không có project.json của bộ vẽ tay.")
        self.root = PurePosixPath(candidates[0]).parent
        self.used = 0

    def read(self, relative):
        if not isinstance(relative, str) or not relative:
            raise ValueError("Đường dẫn trong project.json không hợp lệ")
        path = PurePosixPath(relative.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Đường dẫn không an toàn trong ZIP: {relative}")
        key = (self.root / path).as_posix() if self.root.as_posix() != "." else path.as_posix()
        item = self.names.get(key)
        if item is None:
            raise ValueError(f"ZIP thiếu file {relative}")
        self.used += item.file_size
        if self.used > MAX_BYTES:
            raise ValueError("Tổng dung lượng tư liệu trong ZIP vượt 1,5 GB.")
        return self.zip.read(item)


def build(pid, archive_path, current):
    """Return (whiteboard, new assets, project changes, warnings, written files)."""
    archive = _Archive(archive_path)
    cfg = json.loads(archive.read("project.json").decode("utf-8-sig"))
    if cfg.get("schema_version") != 1 or not isinstance(cfg.get("scenes"), list):
        raise ValueError("project.json không đúng định dạng schema_version 1 của bộ vẽ tay.")
    video = cfg.get("video") or {}
    width, height = int(video.get("width", 1920)), int(video.get("height", 1080))
    fps = float(video.get("fps", 30)) or 30
    warnings, written, assets, by_path = [], [], [], {}
    directory = store.project_dir(pid) / "assets"
    directory.mkdir(parents=True, exist_ok=True)

    def add(relative, role, name=None):
        if relative in by_path:
            return by_path[relative]
        suffix = PurePosixPath(relative).suffix.lower()
        allowed = AUDIO_SUFFIXES if role in ("voice", "music") else IMAGE_SUFFIXES
        if suffix not in allowed:
            raise ValueError(f"Định dạng file không hỗ trợ: {relative}")
        path = directory / f"wbi_{uid()}{suffix}"
        path.write_bytes(archive.read(relative))
        written.append(path)
        asset = media.register(pid, path, name or PurePosixPath(relative).name, role)
        assets.append(asset)
        by_path[relative] = asset
        return asset

    def optional(relative, role, label, name=None):
        try:
            return add(relative, role, name)
        except ValueError as exc:
            if "ZIP thiếu file" not in str(exc):
                raise
            warnings.append(f"Không thấy {label} trong ZIP ({relative}); bỏ qua.")
            return None

    case = str(cfg.get("case", "nam")).lower()
    if case not in ("nam", "nu"):
        warnings.append("Case đối thoại chưa hỗ trợ; dự án được nhập theo case Nam.")
        case = "nam"
    board = Whiteboard(case=case, aspect="16:9" if width >= height else "9:16",
                       background=video.get("background", "#F6F1E3"),
                       music_volume=float((cfg.get("audio") or {}).get("music_volume", 0.48)),
                       transition_frames=int(cfg.get("transition_frames", 6)))
    changes = {}
    if cfg.get("voice"):
        changes["voice_id"] = add(cfg["voice"], "voice").id
    if cfg.get("music") and (music := optional(cfg["music"], "music", "nhạc nền")):
        changes["music_id"] = music.id
    product_path = cfg.get("product_original")
    if product_path and (product := optional(product_path, "product", "ảnh sản phẩm",
                                             "Sản phẩm" + PurePosixPath(product_path).suffix)):
        board.product_id = product.id
    else:
        product_path = None
    character = cfg.get("story_character") or {}
    if character.get("reference") and (reference := optional(character["reference"], "illustration",
                                                             "ảnh nhân vật khóa", "Nhân vật khóa.png")):
        board.character_reference_id = reference.id
        try:
            profile = json.loads(archive.read(character["profile"]).decode("utf-8-sig")) if character.get("profile") else {}
            design = profile.get("design", profile)
            board.character_description = ". ".join(
                str(design[key]) for key in ("age", "face", "hair", "clothing", "drawing_description")
                if design.get(key))[:1500]
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    overlays = [item for item in cfg.get("overlays", []) if isinstance(item, dict)]
    scenes = []
    for index, raw in enumerate(cfg["scenes"]):
        start = raw["start_frame"] / fps if "start_frame" in raw else float(raw.get("start", 0))
        end = raw["end_frame"] / fps if "end_frame" in raw else float(raw.get("end", start + 3))
        image = add(raw["image"], "illustration", f"Cảnh {index + 1}{PurePosixPath(raw['image']).suffix}")
        crop = None
        if raw.get("crop"):
            x0, y0, x1, y1 = (float(value) for value in raw["crop"])
            crop = WBRect(x=x0, y=y0, w=x1 - x0, h=y1 - y0)
        box = raw.get("image_box") or [0, 0, width, height]
        elements = []
        for element in raw.get("elements", [])[:24]:
            region, reveal = element["region"], element.get("reveal") or {}
            elements.append(WBElement(
                region=_rect(region["x"], region["y"], region["width"], region["height"], width, height),
                start_ms=reveal.get("startMs"), duration_ms=reveal.get("durationMs") or None))
        titles = []
        for title in sorted(raw.get("titles", []), key=lambda item: item.get("start", 0))[:3]:
            color = str(title.get("color", "#2B2A28")).upper()
            titles.append(WBTitle(text=str(title["text"])[:80], accent=color not in ("#2B2A28", "#716C63"),
                                  start=max(0.0, float(title.get("start", start)) - start)))
        product = any(item.get("image") == product_path and item.get("start", 0) < end and item.get("end", 0) > start
                      for item in overlays)
        scenes.append(WBScene(
            id=str(raw.get("id") or f"s{index + 1:02d}")[:64], start=round(start, 3), end=round(end, 3),
            narration=str(raw.get("narration", ""))[:1000], image_id=image.id, crop=crop, titles=titles,
            has_character=bool(raw.get("has_story_character", False)), product=product and bool(product_path),
            layout=WBLayout(aspect=board.aspect, image_box=_rect(*box, width, height), elements=elements)))
    board.scenes = scenes
    if overlays:
        warnings.append("Ảnh sản phẩm được đặt lại tự động theo bố cục ClipForge.")
    if cfg.get("captions") or cfg.get("effects"):
        warnings.append("Chữ chạy dưới cùng và hiệu ứng xoay/nét phụ của bộ vẽ tay chưa được nhập.")
    transcript = None
    try:
        transcript = json.loads(archive.read("transcript.json").decode("utf-8-sig"))
    except (ValueError, KeyError):
        transcript = None
    if transcript and not current.cues_edited:
        cues = [Cue(start=float(row["start"]), end=float(row["end"]), text=str(row["text"]).strip()[:1000])
                for row in transcript.get("segments", []) if str(row.get("text", "")).strip()
                and float(row["end"]) > float(row["start"])][:1000]
        if cues:
            changes.update(cues=cues, cue_timing="transcribed", cues_edited=False, cues_stale=False)
            if not current.script.strip():
                changes["script"] = " ".join(cue.text for cue in cues)[:12000]
    return board, assets, changes, warnings, written


def discard(written):
    for path in written:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
