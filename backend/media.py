import hashlib, json, os, subprocess, time, shutil, math
from pathlib import Path
import av, cv2, numpy as np
from PIL import Image
import imageio_ffmpeg
from .models import Asset, Scene, uid
from . import store
from .store import project_dir, asset_path


def ffmpeg():
    return (
        os.environ.get("FFMPEG_BINARY")
        or shutil.which("ffmpeg")
        or imageio_ffmpeg.get_ffmpeg_exe()
    )


def run_ff(args, cwd=None, job=None, timeout=1800):
    import tempfile

    # File-backed stderr avoids pipe deadlocks during long encodes.
    with tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            [ffmpeg(), "-hide_banner", "-nostdin", "-y", *map(str, args)],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if job:
                    job.check()
                if time.monotonic() - started > timeout:
                    raise ValueError("FFmpeg vượt thời gian xử lý cho phép")
                time.sleep(0.15)
            if process.returncode:
                err.seek(0)
                tail = err.read().decode("utf-8", errors="replace")[-1800:]
                raise ValueError("Không xử lý được media: " + tail)
        except BaseException:
            process.kill()
            process.wait()
            raise


def probe(path):
    try:
        with Image.open(path) as im:
            if im.format in ["PNG", "JPEG", "WEBP", "BMP"]:
                return dict(
                    media="image",
                    duration=0,
                    width=im.width,
                    height=im.height,
                    fps=0,
                    has_audio=False,
                )
    except Exception:
        pass
    try:
        with av.open(str(path)) as container:
            video = next(iter(container.streams.video), None)
            audio = next(iter(container.streams.audio), None)
            duration = (container.duration or 0) / av.time_base
            if not duration:
                streams = [
                    s for s in [video, audio] if s and s.duration and s.time_base
                ]
                duration = max(
                    (float(s.duration * s.time_base) for s in streams), default=0
                )
            if not video and not audio:
                raise ValueError("File không chứa hình ảnh hoặc âm thanh")
            if duration <= 0 or duration > 7200:
                raise ValueError("Tư liệu phải có thời lượng từ 0 đến 120 phút")
            return dict(
                media="video" if video else "audio",
                duration=round(duration, 3),
                width=video.width if video else 0,
                height=video.height if video else 0,
                fps=float(video.average_rate or 30) if video else 0,
                has_audio=bool(audio),
            )
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(
            "Không đọc được file media. Hãy dùng MP4, MOV, MP3, WAV, PNG hoặc JPG."
        ) from e


def thumbnail(path, dest, seconds=0, job=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-ss",
        str(max(0, seconds)),
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        "scale=360:-2",
        dest,
    ]
    run_ff(args, job=job)
    if not dest.exists():
        raise ValueError("Không tạo được ảnh xem trước")


def register(pid, path, name, role, job=None, thumbnail_dir=None):
    info = probe(path)
    if role in ["voice", "music"] and not info["has_audio"]:
        raise ValueError("File này không chứa âm thanh")
    if role == "reference" and info["media"] != "video":
        raise ValueError("Video mẫu phải là file video")
    if role == "overlay" and info["media"] != "image":
        raise ValueError("Biểu tượng/logo cần là PNG, JPG hoặc WEBP")
    if role == "source" and info["media"] == "audio":
        raise ValueError("Chọn vai trò Giọng đọc hoặc Nhạc nền cho file âm thanh")
    if role == "voice" and info["duration"] > 180:
        raise ValueError("Giọng đọc đầu ra tối đa 180 giây")
    a = Asset(name=name, filename=path.name, role=role, **info)
    if a.media != "audio":
        a.thumbnail = f"{a.id}.jpg"
        thumbnail_root = thumbnail_dir or project_dir(pid) / "thumbs"
        thumbnail(path, thumbnail_root / a.thumbnail, min(0.2, a.duration / 2), job)
    return a


ANALYSIS_VERSION = "scene-v5-display-rotation-normalized-pts-0.4s-hist32"


def _frame_seconds(frame, fallback):
    if frame.pts is not None and frame.time_base is not None:
        return max(0.0, float(frame.pts * frame.time_base))
    if frame.time is not None:
        return max(0.0, float(frame.time))
    return fallback


def _rotation(stream):
    try:
        return int(round(float(stream.metadata.get("rotate", "0")))) % 360
    except (TypeError, ValueError):
        return 0


def _frame_rotation(frame, fallback=0):
    try:
        value = int(round(float(frame.rotation))) % 360
        return value or fallback
    except (AttributeError, TypeError, ValueError):
        return fallback


def _rotate_frame(frame, rotation):
    if rotation == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rotation == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    return frame


def _analysis_cache_path(asset, source):
    stat = source.stat()
    identity = f"{ANALYSIS_VERSION}|{asset.id}|{asset.filename}|{stat.st_size}|{stat.st_mtime_ns}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    return store.ROOT / "cache" / "scene-analysis" / f"{key}.json"


def _thumbnail_signature(scenes):
    return hashlib.sha256(
        json.dumps(
            [(s.id, s.start, s.end, s.thumbnail) for s in scenes], separators=(",", ":")
        ).encode()
    ).hexdigest()


def _save_scene_thumbnail(pid, asset, scenes, job=None):
    if not scenes:
        return
    path = asset_path(pid, asset)
    target_root = project_dir(pid) / "thumbs"
    target_root.mkdir(parents=True, exist_ok=True)
    targets = [(scene.start + scene.end) / 2 for scene in scenes]
    try:
        container = av.open(str(path))
        stream = next(iter(container.streams.video), None)
        if stream is None:
            raise ValueError("Không tìm thấy luồng hình ảnh")
        rotation = _rotation(stream)
        average = float(stream.average_rate or asset.fps or 30)
        last_frame = None
        fallback_index = 0
        target_index = 0
        current = None
        time_origin = None

        def sized(rgb):
            h, w = rgb.shape[:2]
            scale = min(360 / max(w, h), 1.0)
            if scale < 1:
                rgb = cv2.resize(
                    rgb,
                    (max(1, round(w * scale)), max(1, round(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            return rgb

        def save(scene, rgb):
            Image.fromarray(rgb).save(
                target_root / scene.thumbnail, format="JPEG", quality=84, optimize=True
            )
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            tiny = cv2.resize(gray, (9, 8))
            bits = (tiny[:, 1:] > tiny[:, :-1]).flatten()
            scene.fingerprint = (
                f'{int("".join("1" if x else "0" for x in bits),2):016x}'
            )
            scene.quality = round(
                min(1, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 800), 3
            )

        try:
            for frame in container.decode(stream):
                if job:
                    job.check()
                rotation = _frame_rotation(frame, rotation)
                seconds = _frame_seconds(frame, fallback_index / max(average, 1))
                if time_origin is None:
                    time_origin = seconds
                seconds = max(0, seconds - time_origin)
                fallback_index += 1
                last_frame = frame
                while target_index < len(targets) and seconds >= targets[target_index]:
                    if current is None:
                        rgb = cv2.cvtColor(
                            _rotate_frame(frame.to_ndarray(format="bgr24"), rotation),
                            cv2.COLOR_BGR2RGB,
                        )
                        current = sized(rgb)
                    save(scenes[target_index], current)
                    target_index += 1
                current = None
                if target_index == len(targets):
                    break
        finally:
            container.close()
        if target_index < len(targets) and last_frame is not None:
            rgb = cv2.cvtColor(
                _rotate_frame(last_frame.to_ndarray(format="bgr24"), rotation),
                cv2.COLOR_BGR2RGB,
            )
            last_image = sized(rgb)
            for index in range(target_index, len(targets)):
                save(scenes[index], last_image)
        try:
            store.atomic(
                target_root / f"{asset.id}.scenes.json",
                {
                    "version": ANALYSIS_VERSION,
                    "signature": _thumbnail_signature(scenes),
                },
            )
        except OSError:
            pass
        return rotation
    except ValueError:
        raise
    except Exception as exc:
        if job and job.cancelled.is_set():
            raise
        raise ValueError("Không tạo được thumbnail cảnh từ video") from exc


def refresh_scene_thumbnails(pid, asset, job=None):
    """Repair pre-upgrade thumbnails while retaining scene IDs and clip references."""
    if asset.media != "video" or not asset.scenes:
        return False
    root = project_dir(pid) / "thumbs"
    try:
        marker = json.loads(
            (root / f"{asset.id}.scenes.json").read_text(encoding="utf-8")
        )
        if (
            marker.get("version") == ANALYSIS_VERSION
            and marker.get("signature") == _thumbnail_signature(asset.scenes)
            and all((root / scene.thumbnail).is_file() for scene in asset.scenes)
        ):
            return False
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    rotation = _save_scene_thumbnail(pid, asset, asset.scenes, job)
    if rotation in (90, 270):
        for scene in asset.scenes:
            scene.ai_labeled = False
    return True


def _cached_scenes(pid, asset, cache_path, job=None):
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if raw.get("version") != ANALYSIS_VERSION:
            return None
        scenes = [Scene.model_validate(item) for item in raw.get("scenes", [])]
        if not scenes:
            return None
        thumb_root = project_dir(pid) / "thumbs"
        if any(not (thumb_root / scene.thumbnail).is_file() for scene in scenes):
            _save_scene_thumbnail(pid, asset, scenes, job)
            from .store import atomic

            try:
                atomic(
                    cache_path,
                    {
                        "version": ANALYSIS_VERSION,
                        "scenes": [scene.model_dump() for scene in scenes],
                    },
                )
            except OSError:
                pass
        return scenes
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def analyze(pid, asset, job=None):
    if asset.media == "image":
        asset.scenes = [
            Scene(
                start=0, end=3, thumbnail=asset.thumbnail, tags=asset.tags or asset.name
            )
        ]
        return asset
    if asset.media != "video":
        return asset
    source = asset_path(pid, asset)
    cache_path = _analysis_cache_path(asset, source)
    cached = _cached_scenes(pid, asset, cache_path, job)
    if cached:
        asset.scenes = cached
        return asset
    step = 0.4
    cuts = [0.0]
    prev = None
    next_sample = 0.0
    fallback_index = 0
    time_origin = None
    last_update = time.monotonic()
    try:
        container = av.open(str(source))
        stream = next(iter(container.streams.video), None)
        if stream is None:
            raise ValueError("Không tìm thấy luồng hình ảnh")
        stream.thread_type = "AUTO"
        rotation = _rotation(stream)
        average = float(stream.average_rate or asset.fps or 30)
        try:
            for frame in container.decode(stream):
                if job:
                    job.check()
                rotation = _frame_rotation(frame, rotation)
                pos = _frame_seconds(frame, fallback_index / max(average, 1))
                if time_origin is None:
                    time_origin = pos
                pos = max(0, pos - time_origin)
                fallback_index += 1
                if pos >= asset.duration:
                    break
                if pos + 1e-6 < next_sample:
                    continue
                small = frame.reformat(
                    width=160, height=90, format="bgr24"
                ).to_ndarray()
                small = _rotate_frame(small, rotation)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if (
                    prev is not None
                    and cv2.compareHist(prev, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.55
                    and pos - cuts[-1] >= 0.8
                ):
                    cuts.append(round(pos, 3))
                prev = hist
                next_sample = pos + step
                if job and time.monotonic() - last_update > 2:
                    job.update(
                        min(24, 5 + 18 * pos / max(asset.duration, 0.01)),
                        f"Phân tích hình ảnh {pos:.1f}/{asset.duration:.1f}s",
                    )
                    last_update = time.monotonic()
        finally:
            container.close()
    except ValueError:
        raise
    except Exception as exc:
        if job and job.cancelled.is_set():
            raise
        raise ValueError("Không giải mã được video để phân tích cảnh") from exc
    cuts.append(asset.duration)
    segments = []
    for start, end in zip(cuts, cuts[1:]):
        length = end - start
        if length < 0.45:
            continue
        # Balanced windows do not cross a detected shot boundary.
        n = 1 if asset.role == "reference" else max(1, math.ceil(length / 3))
        for i in range(n):
            segments.append((start + length * i / n, start + length * (i + 1) / n))
    scenes = []
    for i, (start, end) in enumerate(segments):
        sid = uid()
        thumb = f"{asset.id}_{sid}.jpg"
        scenes.append(
            Scene(
                id=sid,
                start=round(start, 3),
                end=round(end, 3),
                thumbnail=thumb,
                tags=asset.tags or asset.name,
            )
        )
    _save_scene_thumbnail(pid, asset, scenes, job)
    asset.scenes = scenes
    from .store import atomic

    try:
        atomic(
            cache_path,
            {
                "version": ANALYSIS_VERSION,
                "scenes": [scene.model_dump() for scene in scenes],
            },
        )
    except OSError:
        pass
    return asset


def proxy_for_preview(pid, asset, job=None):
    """Build/reuse a bounded 720p proxy only for heavyweight source video previews."""
    if asset.media != "video":
        return asset_path(pid, asset)
    source = asset_path(pid, asset)
    try:
        stat = source.stat()
    except OSError:
        return source
    if max(asset.width, asset.height) <= 1280 and stat.st_size < 200 * 1024 * 1024:
        return source
    identity = (
        f"proxy-v1|{asset.id}|{stat.st_size}|{stat.st_mtime_ns}|1280|libx264-crf30-vfr"
    )
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache = store.ROOT / "cache" / "proxies" / f"{key}.mp4"
    if cache.is_file() and cache.stat().st_size > 0:
        try:
            cached = probe(cache)
            if cached.get("media") == "video" and cached.get("duration", 0) > 0:
                return cache
        except (OSError, ValueError, RuntimeError):
            pass
        cache.unlink(missing_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(f'{key}.{getattr(job,"id","preview")}.tmp.mp4')
    if job:
        job.update(6, f"Tạo proxy xem trước · {asset.name}")
    try:
        run_ff(
            [
                "-i",
                source,
                "-vf",
                "scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-an",
                "-vsync",
                "0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "30",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                temporary,
            ],
            job=job,
        )
        probe(temporary)
        os.replace(temporary, cache)
        return cache
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def extract_audio(source, dest, job=None):
    run_ff(
        ["-i", source, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dest],
        job=job,
    )


def waveform_peaks(pid, asset, max_points=2000):
    """Return cached, time-aligned audio peaks at a bounded display resolution."""
    if not asset.has_audio:
        raise ValueError("Tư liệu này không có âm thanh để hiển thị dạng sóng")
    source = asset_path(pid, asset)
    stat = source.stat()
    identity = f"waveform-v1:{asset.id}:{stat.st_size}:{stat.st_mtime_ns}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    cache = store.ROOT / "cache" / "waveforms" / f"{key}.json"
    raw = None
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
        if raw.get("version") != 1 or not isinstance(raw.get("peaks"), list):
            raw = None
    except (OSError, ValueError, TypeError, AttributeError):
        raw = None
    if raw is None:
        try:
            container = av.open(str(source))
            audio_stream = next(iter(container.streams.audio), None)
            if audio_stream is None:
                raise ValueError("Không tìm thấy luồng âm thanh trong tư liệu này")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=8000)
            samples = np.empty(0, dtype=np.int16)
            peaks = []

            def consume(frames):
                nonlocal samples
                for decoded in frames or []:
                    chunk = (
                        decoded.to_ndarray().reshape(-1).astype(np.int16, copy=False)
                    )
                    if not len(chunk):
                        continue
                    samples = np.concatenate((samples, chunk))
                    usable = (len(samples) // 80) * 80
                    if usable:
                        block = samples[:usable].astype(np.int32).reshape(-1, 80)
                        peaks.extend(
                            (
                                np.abs(block).max(axis=1).astype(np.float32) / 32768
                            ).tolist()
                        )
                        peaks[-(usable // 80) :] = [
                            round(float(value), 5) for value in peaks[-(usable // 80) :]
                        ]
                        samples = samples[usable:]

            try:
                for frame in container.decode(audio_stream):
                    consume(resampler.resample(frame))
                consume(resampler.resample(None))
            finally:
                container.close()
            if len(samples):
                peaks.append(
                    round(float(np.max(np.abs(samples.astype(np.int32))) / 32768), 5)
                )
            if not peaks:
                raise ValueError("Luồng âm thanh không có mẫu để phân tích")
            raw = {
                "version": 1,
                "sample_rate": 8000,
                "peak_rate": 100,
                "duration": asset.duration,
                "peaks": peaks,
            }
            store.atomic(cache, raw)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Không thể tạo waveform cho tư liệu âm thanh này") from exc
    peaks = raw["peaks"]
    max_points = max(64, min(6000, int(max_points)))
    if len(peaks) > max_points:
        stride = math.ceil(len(peaks) / max_points)
        peaks = [
            max(peaks[index : index + stride]) for index in range(0, len(peaks), stride)
        ]
    return {
        "asset_id": asset.id,
        "duration": asset.duration,
        "peak_rate": raw.get("peak_rate", 100),
        "peaks": peaks,
    }


def dimensions(aspect, resolution):
    s = int(resolution)
    return (
        (s, int(s * 16 / 9) // 2 * 2)
        if aspect == "9:16"
        else ((int(s * 16 / 9) // 2 * 2, s) if aspect == "16:9" else (s, s))
    )
