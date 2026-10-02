"""Local, conservative face screening. Unknown results never enter the clean pool."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import av
import cv2

from . import media, store
from .timeline_math import clip_starts

MODEL = Path(__file__).parent / "assets" / "face_detection_yunet_2026may.onnx"
MODEL_SHA = "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0"
VERSION = "yunet-v1-8fps-960px-score060"
STEP = 1 / 8


class Detector:
    def __init__(self):
        if (
            not MODEL.is_file()
            or hashlib.sha256(MODEL.read_bytes()).hexdigest() != MODEL_SHA
        ):
            raise ValueError(
                "Thiếu mô hình lọc khuôn mặt. Cài lại ClipForge để khôi phục mô hình."
            )
        self.model = cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), 0.6)

    def has_face(self, image):
        height, width = image.shape[:2]
        scale = min(1, 960 / max(height, width))
        if scale < 1:
            image = cv2.resize(
                image, (max(1, round(width * scale)), max(1, round(height * scale)))
            )
        self.model.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self.model.detect(image)
        return faces is not None and len(faces) > 0


def _key(pid, asset, start, end, crop=None):
    source = store.asset_path(pid, asset)
    stat = source.stat()
    data = [
        VERSION,
        MODEL_SHA,
        str(source.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
        round(start, 6),
        round(end, 6),
        crop,
    ]
    return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()


def _read(key):
    try:
        result = json.loads(
            (store.ROOT / "cache" / "face-scans" / f"{key}.json").read_text(
                encoding="utf-8"
            )
        )
        if result.get("key") == key and result.get("status") in ("clear", "present"):
            return result
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _visible(image, crop):
    if not crop:
        return image
    # Same cover geometry as the renderer, after display rotation.
    aspect, x, y = crop
    h, w = image.shape[:2]
    if w / h > aspect:
        cw = max(1, round(h * aspect))
        left = round((w - cw) * x)
        return image[:, left : left + cw]
    ch = max(1, round(w / aspect))
    top = round((h - ch) * y)
    return image[top : top + ch, :]


def scan_range(pid, asset, start, end, job=None, detector=None, crop=None):
    key = _key(pid, asset, start, end, crop)
    cached = _read(key)
    if cached:
        return cached
    detector = detector or Detector()
    samples = 0
    status = "unknown"
    first = last = None
    try:
        path = store.asset_path(pid, asset)
        if asset.media == "image":
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError("Không đọc được ảnh")
            status = "present" if detector.has_face(_visible(image, crop)) else "clear"
            samples = 1
        else:
            with av.open(str(path)) as container:
                stream = next(iter(container.streams.video), None)
                if stream is None:
                    raise ValueError("Không có luồng video")
                stream.thread_type = "AUTO"
                fps = float(stream.average_rate or asset.fps or 30)
                origin = float((stream.start_time or 0) * stream.time_base)
                container.seek(int((start + origin) / stream.time_base), stream=stream)
                rotation = media._rotation(stream)
                next_sample = start
                previous = None

                def check(frame, seconds):
                    nonlocal samples, status
                    image = media._rotate_frame(
                        frame.to_ndarray(format="bgr24"),
                        media._frame_rotation(frame, rotation),
                    )
                    samples += 1
                    if detector.has_face(_visible(image, crop)):
                        status = "present"

                for frame in container.decode(stream):
                    if job:
                        job.check()
                    seconds = media._frame_seconds(frame, 0) - origin
                    if seconds < start - 1e-6:
                        continue
                    if seconds >= end - 1e-6:
                        break
                    first = seconds if first is None else first
                    last = seconds
                    previous = frame
                    if seconds + 1e-6 >= next_sample:
                        check(frame, seconds)
                        next_sample = seconds + STEP
                        if status == "present":
                            break
                # Include the last decoded frame, even when not on the sampling grid.
                if status != "present" and previous is not None:
                    check(previous, last)
                    tolerance = max(0.05, 2 / max(1, fps))
                    if first <= start + tolerance and last >= end - tolerance:
                        status = "clear"
    except Exception:
        if job:
            job.check()
        status = "unknown"
    result = {"key": key, "status": status, "samples": samples}
    if status != "unknown":
        try:
            store.atomic(store.ROOT / "cache" / "face-scans" / f"{key}.json", result)
        except OSError:
            pass
    return result


def clean_pool(p, job):
    sources = [
        a
        for a in p.assets
        if a.role == "source" and not a.deleted and a.media in ("image", "video")
    ]
    detector = Detector()
    pool = []
    for index, asset in enumerate(sources):
        job.check()
        job.update(
            5 + 55 * index / max(1, len(sources)), "Lọc khuôn mặt: " + asset.name
        )
        if not asset.scenes:
            media.analyze(p.id, asset, job)
        for scene in asset.scenes:
            result = scan_range(p.id, asset, scene.start, scene.end, job, detector)
            scene.face_status = result["status"]
            scene.face_scan = result["key"]
            if scene.face_status == "clear":
                pool.append((asset, scene))
    return pool


def clip_range(p, clip, asset):
    if asset.media == "image":
        return 0, 3, None
    end = min(asset.duration, clip.source_start + clip.duration * clip.speed)
    if clip.scene_id:
        scene = next((s for s in asset.scenes if s.id == clip.scene_id), None)
        if scene:
            end = min(end, scene.end)
    vp = p.template.viewport
    aspect = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1}[p.aspect] * vp.w / vp.h
    crop = [round(aspect, 8), clip.crop_x, clip.crop_y] if clip.fit == "cover" else None
    return clip.source_start, end, crop


def cached_clip_status(p, clip):
    try:
        asset = store.get_asset(p, clip.asset_id)
        start, end, crop = clip_range(p, clip, asset)
        scene = next((s for s in asset.scenes if s.id == clip.scene_id), None)
        if (
            scene
            and scene.face_status == "clear"
            and start >= scene.start
            and end <= scene.end
            and scene.face_scan == _key(p.id, asset, scene.start, scene.end)
        ):
            return "clear"
        result = _read(_key(p.id, asset, start, end, crop))
        return result["status"] if result else "unknown"
    except (OSError, ValueError):
        return "unknown"


def scan_clip(p, clip, job, detector=None):
    asset = store.get_asset(p, clip.asset_id)
    start, end, crop = clip_range(p, clip, asset)
    return scan_range(p.id, asset, start, end, job, detector, crop)["status"]


def check_locked(p, job):
    detector = None
    for index, clip in enumerate(p.clips):
        if not clip.locked:
            continue
        detector = detector or Detector()
        if scan_clip(p, clip, job, detector) != "clear":
            raise ValueError(
                f"Cảnh {index+1} đã khóa có mặt hoặc chưa kiểm tra được. Mở khóa rồi lọc lại."
            )


def filter_timeline(p, job):
    """Replace only flagged clips, keeping clip IDs, duration and the shared clock."""
    from .planner import tokens

    stop = tokens(
        "một các những là và của cho có trong trên đang người tay bên cạnh hình ảnh cảnh cận nền mp4 png webp motion"
    )
    meaningful = lambda text: tokens(text) - stop
    p.avoid_faces = True
    pool = clean_pool(p, job)
    detector = Detector()
    starts = clip_starts(p.clips)
    replacements = []
    used = Counter(clip.scene_id for clip in p.clips)
    for index, clip in enumerate(p.clips):
        job.check()
        job.update(
            65 + 30 * index / max(1, len(p.clips)),
            f"Kiểm tra cảnh {index+1}/{len(p.clips)}",
        )
        if scan_clip(p, clip, job, detector) == "clear":
            continue
        if clip.locked:
            raise ValueError(
                f"Cảnh {index+1} đã khóa có mặt hoặc chưa kiểm tra được. Mở khóa rồi lọc lại."
            )
        old = store.get_asset(p, clip.asset_id)
        text = " ".join(
            c.text
            for c in p.cues
            if c.start < starts[index] + clip.duration and c.end > starts[index]
        )
        old_scene = next((s for s in old.scenes if s.id == clip.scene_id), None)
        title_words = meaningful(clip.title)
        context = meaningful(
            old.name + " " + old.tags + " " + (old_scene.tags if old_scene else "")
        )
        speech = meaningful(text)
        candidates = [
            (a, s)
            for a, s in pool
            if a.media == "image" or s.end - s.start - 0.01 >= clip.duration * 0.25
        ]
        if not candidates:
            raise ValueError(
                "Không đủ cảnh không có mặt để thay. Thêm ảnh sản phẩm hoặc video chỉ quay tay/sản phẩm rồi lọc lại."
            )

        def score(pair):
            a, s = pair
            labels = meaningful(s.tags + " " + a.tags + " " + a.name)
            return (
                80 * len(title_words & labels) / max(1, len(title_words))
                + 15 * len(context & labels) / max(1, len(context))
                + 5 * len(speech & labels) / max(1, len(speech))
                + s.quality
                + (3 if a.id == old.id else 0)
                - used[s.id] * 0.6
            )

        asset, scene = max(candidates, key=score)
        replacement = clip.model_copy(deep=True)
        replacement.asset_id = asset.id
        replacement.scene_id = scene.id
        replacement.source_start = scene.start
        if asset.media == "video":
            replacement.speed = min(
                clip.speed, (scene.end - scene.start - 0.01) / clip.duration
            )
        else:
            replacement.speed = 1
        replacement.crop_x = 0.5
        replacement.crop_y = 0.5
        replacement.text_regions = []
        replacement.text_regions_override = False
        replacement.text_mode = "inherit"
        replacements.append((index, replacement))
        used[scene.id] += 1
    # A failed scan/locked conflict never leaves a partially replaced timeline.
    for index, clip in replacements:
        p.clips[index] = clip
    p.warnings = [w for w in p.warnings if not w.startswith("Lọc khuôn mặt:")]
    p.warnings.append(
        f"Lọc khuôn mặt: đã thay {len(replacements)} cảnh; giữ nguyên nhịp và lời đọc. Kiểm tra video trước khi đăng."
    )
    job.update(98, f"Đã thay {len(replacements)} cảnh có mặt hoặc chưa kiểm tra được")
    return p
