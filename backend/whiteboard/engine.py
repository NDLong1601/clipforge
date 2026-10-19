"""Hand-drawn whiteboard export, ported from the srt-whiteboard-animation-custom kit.

Stages follow the kit's pipeline.py: prepare a flat canvas per scene, draw it
with the vendored geeklee renderer in a child process, compose transitions,
keyword titles and the product photo, then mix the voice (and music) with the
kit's loudness chain. Scene clips are cached by content signature.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .. import font_manager, media, processes, store
from ..store import now
from . import regions

VERSION = 1
FPS = 30
HERE = Path(__file__).resolve().parent
HAND = HERE.parent / "assets" / "whiteboard" / "hand.png"
WORKER = HERE / "worker.py"
CANVAS = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
INK, ACCENT = "#2B2A28", "#AC5437"
# Illustration takes ~80% of the board (kit visual-priority.md). In 16:9 the
# keyword band sits bottom-right so the portrait narrator never covers it.
LAYOUTS = {
    "16:9": {"image": (64, 16, 1792, 884), "image_with_product": (64, 16, 1120, 884),
             "product": (1220, 110, 640, 700), "titles": (906, 1070, 1420, 900, 64)},
    "9:16": {"image": (40, 470, 1000, 1180), "image_with_product": (40, 470, 1000, 780),
             "product": (190, 1270, 700, 480), "titles": (150, 450, 540, 960, 84)},
}
MIN_SCENE = 0.8
TITLE_FADE, PRODUCT_FADE, FADE_OUT = 0.16, 0.28, 0.2
VOICE_FILTER = "highpass=f=70,lowpass=f=11000,loudnorm=I=-15:TP=-1.5:LRA=7"


def rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _font_file():
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf"
    if windows.is_file():
        return str(windows)
    path, _, _ = font_manager.resolve("Segoe UI", bold=True)
    if path is None:
        raise ValueError("Không tìm thấy font đậm hỗ trợ tiếng Việt để viết chữ trong cảnh.")
    return str(path)


def ease(value):
    return 1 - (1 - max(0.0, min(1.0, value))) ** 3


def _voice(project):
    if not project.voice_id:
        raise ValueError("Chưa có giọng đọc. Tạo giọng AI hoặc nhập file giọng ở tab Âm thanh.")
    asset = store.get_asset(project, project.voice_id)
    path = store.asset_path(project.id, asset)
    if not path.is_file():
        raise ValueError("File giọng đọc đã bị xóa khỏi dự án.")
    return path, media.probe(path)["duration"]


def timeline(project, duration):
    """Contiguous frame ranges: each scene runs until the next one starts."""
    scenes = project.whiteboard.scenes
    if not scenes:
        raise ValueError("Chưa có cảnh vẽ tay. Lập storyboard hoặc thêm cảnh trước khi xuất.")
    frames = math.ceil(duration * FPS - 1e-6)
    starts = [0] + [round(scene.start * FPS) for scene in scenes[1:]] + [frames]
    for index, scene in enumerate(scenes):
        if not scene.image_id:
            raise ValueError(f"Cảnh {index + 1} chưa có tranh. Tạo ảnh AI hoặc chọn ảnh cho cảnh.")
        if starts[index + 1] - starts[index] < MIN_SCENE * FPS:
            raise ValueError(f"Cảnh {index + 1} quá ngắn hoặc bắt đầu sau khi giọng đọc kết thúc "
                             f"(cần tối thiểu {MIN_SCENE:g} giây mỗi cảnh, giọng dài {duration:.1f}s).")
    return frames, list(zip(starts, starts[1:]))


def _rect(fractions, size):
    width, height = size
    x, y = round(fractions.x * width), round(fractions.y * height)
    return x, y, max(1, min(width - x, round(fractions.w * width))), max(1, min(height - y, round(fractions.h * height)))


def scene_layout(board, scene, product_image):
    """Image box, product rect and title area in canvas pixels for one scene."""
    size = CANVAS[board.aspect]
    preset = LAYOUTS[board.aspect]
    product = scene.product and product_image is not None
    custom = scene.layout if scene.layout and scene.layout.aspect == board.aspect else None
    if not custom:
        return {"size": size, "box": preset["image_with_product" if product else "image"],
                "product": preset["product"] if product else None, "titles": preset["titles"], "custom": None}
    box = _rect(custom.image_box, size)
    x0, y0, x1, y1 = _free_area(size, box)
    base = 90 if size[0] > size[1] else 84
    split = y0 + round((y1 - y0) * 0.45) if product else y1
    titles = (y0, split, round((x0 + x1) / 2), max(120, x1 - x0 - 60), base)
    rect = (x0 + 30, split + 10, max(60, x1 - x0 - 60), max(60, y1 - split - 20)) if product else None
    return {"size": size, "box": box, "product": rect, "titles": titles, "custom": custom}


def _free_area(size, box):
    """Largest margin beside a hand-placed image, as (x0, y0, x1, y1)."""
    width, height = size
    x, y, w, h = box
    areas = [
        (x * height, (0, 40, x, height - 40)),
        ((width - x - w) * height, (x + w, 40, width, height - 40)),
        (width * y, (0, 20, width, max(21, y - 10))),
        (width * (height - y - h), (0, min(height - 21, y + h + 10), width, height - 20)),
    ]
    return max(areas, key=lambda item: item[0])[1]


def _flatten(art, background):
    """Composite onto the canvas colour and absorb a light flat backdrop into it."""
    canvas = Image.new("RGBA", art.size, (*background, 255))
    canvas.alpha_composite(art.convert("RGBA"))
    pixels = np.array(canvas.convert("RGB")).astype(np.int16)
    edge = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
    backdrop = np.median(edge, axis=0)
    if backdrop.mean() > 200:
        distance = np.abs(pixels - backdrop).sum(axis=2)
        blend = np.clip((distance - 24) / 36, 0, 1)[..., None]
        pixels = pixels * blend + np.array(background) * (1 - blend)
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8))


def scene_canvas(project, scene, layout, background):
    asset = store.get_asset(project, scene.image_id)
    with Image.open(store.asset_path(project.id, asset)) as source:
        art = ImageOps.exif_transpose(source).convert("RGBA")
    if scene.crop:
        x, y, w, h = _rect(scene.crop, art.size)
        art = art.crop((x, y, x + w, y + h))
    art = _flatten(art, background)
    canvas = Image.new("RGB", layout["size"], background)
    x, y, w, h = layout["box"]
    art = ImageOps.contain(art, (w, h), Image.Resampling.LANCZOS)
    canvas.paste(art, (x + (w - art.width) // 2, y + (h - art.height) // 2))
    return canvas


def elements(canvas, layout, background, duration_ms):
    """Kit annotation elements; regions cover >= 99.5% of the ink."""
    pixels = np.array(canvas)
    width, height = layout["size"]
    custom = layout["custom"]
    if custom and custom.elements:
        rects = [_rect(element.region, (width, height)) for element in custom.elements]
        timed = [(element.start_ms, element.duration_ms) for element in custom.elements]
    else:
        rects, timed = regions.find(pixels, background, layout["box"]), []
    fraction, missing = regions.uncovered(pixels, rects)
    if fraction > regions.MAX_UNCOVERED and missing:
        rects.append(missing)
        timed = []
    if not timed or any(value is None for pair in timed for value in pair) or not _sequential(timed, duration_ms):
        timed = regions.timing(rects, duration_ms)
    return [{"id": f"r{index + 1}", "sequence": index + 1,
             "region": {"x": x, "y": y, "width": w, "height": h},
             "reveal": {"startMs": start, "durationMs": length, "protectedRegions": []}}
            for index, ((x, y, w, h), (start, length)) in enumerate(zip(rects, timed))]


def _sequential(timed, duration_ms):
    end = 0
    for start, length in sorted(timed):
        if start < end - 1 or length <= 0:
            return False
        end = start + length
    return end <= duration_ms


def prepare(project, scene, frames, product_image=None):
    """Return (canvas image, annotation, layout) for one scene."""
    board = project.whiteboard
    background = rgb(board.background)
    layout = scene_layout(board, scene, product_image)
    canvas = scene_canvas(project, scene, layout, background)
    duration_ms = round(frames * 1000 / FPS)
    annotation = {"sceneId": scene.id, "canvas": {"width": layout["size"][0], "height": layout["size"][1]},
                  "sceneDurationMs": duration_ms, "storyBasis": scene.narration,
                  "elements": elements(canvas, layout, background, duration_ms)}
    return canvas, annotation, layout


def _font(size, cache={}):
    if size not in cache:
        cache[size] = ImageFont.truetype(_font_file(), size)
    return cache[size]


def title_layers(scene, layout, scene_seconds):
    """Pre-rendered keyword lines: (rgba array, x, y, start seconds after scene start)."""
    if not scene.titles:
        return []
    top, bottom, center, max_width, base = layout["titles"]
    count = len(scene.titles)
    line = min(base * 1.25, (bottom - top) / count)
    result = []
    first = top + ((bottom - top) - line * count) / 2
    for index, title in enumerate(scene.titles):
        size = max(16, round(line / 1.25))
        font = _font(size)
        while font.getlength(title.text) > max_width and size > 16:
            size -= 2
            font = _font(size)
        box = font.getbbox(title.text)
        layer = Image.new("RGBA", (box[2] - box[0] + 14, box[3] - box[1] + 14))
        ImageDraw.Draw(layer).text((7 - box[0], 7 - box[1]), title.text, font=font,
                                   fill=(*rgb(ACCENT if title.accent else INK), 255))
        x = round(center - layer.width / 2)
        y = round(first + index * line + (line - layer.height) / 2)
        start = title.start if title.start is not None else 0.15 + index * min(0.45, scene_seconds * 0.15)
        x = max(0, min(layout["size"][0] - layer.width, x))
        y = max(0, min(layout["size"][1] - layer.height, y))
        result.append((np.array(layer), x, y, min(start, max(0.0, scene_seconds - 0.3))))
    return result


def product_layer(path, rect):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    image = ImageOps.contain(image, rect[2:], Image.Resampling.LANCZOS)
    x = rect[0] + (rect[2] - image.width) // 2
    y = rect[1] + (rect[3] - image.height) // 2
    return np.array(image), x, y


def _blend(frame, layer, x, y, alpha):
    h, w = layer.shape[:2]
    region = frame[y:y + h, x:x + w]
    h, w = region.shape[:2]
    mask = layer[:h, :w, 3:4].astype(np.float32) * (alpha / 255)
    region[:] = (region * (1 - mask) + layer[:h, :w, :3] * mask).astype(np.uint8)


def signature(canvas_bytes, annotation, size, background):
    digest = hashlib.sha256()
    digest.update(json.dumps({"version": VERSION, "fps": FPS, "size": size, "background": background,
                              "annotation": annotation}, sort_keys=True).encode())
    digest.update(canvas_bytes)
    for path in [HAND, WORKER, HERE / "vendor" / "render_stream_whiteboard.py", HERE / "vendor" / "stream_render.py"]:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def draw_scene(work, scene_id, canvas_path, annotation, frames, size, background, cache, job):
    """Render one scene clip (cached); returns the clip path."""
    key = signature(canvas_path.read_bytes(), annotation, size, background)
    clip = cache / f"{key}.mp4"
    if clip.is_file():
        return clip, True
    annotation_path = work / f"{scene_id}.annotation.json"
    annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")
    partial = work / f"{scene_id}.partial.mp4"
    config = work / f"{scene_id}.worker.json"
    config.write_text(json.dumps({
        "image": str(canvas_path), "annotation": str(annotation_path), "output": str(partial),
        "frames": frames, "width": size[0], "height": size[1], "fps": FPS, "background": background,
        "hand": str(HAND), "ffmpeg": media.ffmpeg(),
    }), encoding="utf-8")
    result = processes.run([sys.executable, "-X", "utf8", str(WORKER), str(config)],
                           timeout=3600, capture_output=True, job=job)
    if result.returncode or not partial.is_file():
        detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        raise ValueError("Không vẽ được cảnh " + scene_id + (": " + " ".join(detail) if detail else ""))
    cache.mkdir(parents=True, exist_ok=True)
    os.replace(partial, clip)
    return clip, False


def _frames(path):
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            yield frame.to_ndarray(format="rgb24")


def srt(project, duration):
    def stamp(seconds):
        ms = round(seconds * 1000)
        hours, ms = divmod(ms, 3600000)
        minutes, ms = divmod(ms, 60000)
        secs, ms = divmod(ms, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"

    rows = [(cue.start, cue.end, cue.text) for cue in project.cues if cue.text.strip()]
    if not rows:
        scenes = project.whiteboard.scenes
        rows = [(scene.start, scenes[index + 1].start if index + 1 < len(scenes) else duration, scene.narration)
                for index, scene in enumerate(scenes) if scene.narration.strip()]
    rows = [(start, min(end, duration), text) for start, end, text in rows if start < duration]
    return "\n\n".join(f"{index + 1}\n{stamp(start)} --> {stamp(end)}\n{text.strip()}"
                       for index, (start, end, text) in enumerate(rows)) + "\n"


def render(project, job):
    board = project.whiteboard
    job.start_step("preflight")
    voice, voice_duration = _voice(project)
    frames, ranges = timeline(project, voice_duration)
    duration = frames / FPS
    size = CANVAS[board.aspect]
    background = rgb(board.background)
    product_image = None
    if board.product_id and any(scene.product for scene in board.scenes):
        product_image = store.asset_path(project.id, store.get_asset(project, board.product_id))
    music = None
    if project.music_id:
        music = store.asset_path(project.id, store.get_asset(project, project.music_id))
    directory = store.project_dir(project.id) / "exports" / job.id
    work = directory / "work"
    work.mkdir(parents=True, exist_ok=True)
    cache = store.project_dir(project.id) / "cache" / "whiteboard"

    job.start_step("prepare-scenes")
    prepared = []
    for index, (scene, (start, end)) in enumerate(zip(board.scenes, ranges)):
        job.update(2 + 8 * index / len(ranges), f"Chuẩn bị cảnh vẽ tay {index + 1}/{len(ranges)}")
        canvas, annotation, layout = prepare(project, scene, end - start, product_image)
        path = work / f"{scene.id}.png"
        canvas.save(path)
        prepared.append((scene, start, end, path, annotation, layout))

    job.start_step("draw-scenes")
    done, hits = [0], [0]

    def draw(item):
        scene, start, end, path, annotation, _ = item
        clip, cached = draw_scene(work, scene.id, path, annotation, end - start, size, board.background, cache, job)
        done[0] += 1
        hits[0] += int(cached)
        job.update(10 + 60 * done[0] / len(prepared), f"Đã vẽ {done[0]}/{len(prepared)} cảnh")
        return clip

    job.update(10, f"Vẽ {len(prepared)} cảnh bằng bàn tay vẽ")
    with ThreadPoolExecutor(max_workers=min(2, len(prepared))) as pool:
        clips = list(pool.map(draw, prepared))

    job.start_step("join-video")
    job.update(72, "Ghép chuyển cảnh, chữ khóa và ảnh sản phẩm")
    visuals = work / "visuals.mp4"
    product = None
    width, height = size
    command = [media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0", "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-tune", "animation",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(visuals)]
    written, previous = 0, None
    bg = np.array(background, dtype=np.float32)
    with processes.command(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as writer:
        try:
            for number, ((scene, start, end, _, _, layout), clip) in enumerate(zip(prepared, clips)):
                seconds = (end - start) / FPS
                titles = title_layers(scene, layout, seconds)
                shown_product = None
                if layout["product"] and product_image is not None:
                    shown_product = product_layer(product_image, layout["product"])
                    product_fade = not (number and prepared[number - 1][0].product)
                last = None
                decoded = _frames(clip)
                for local in range(end - start):
                    frame = next(decoded, None)
                    if frame is None:
                        raise ValueError(f"Clip cảnh {scene.id} ngắn hơn timeline")
                    last = frame
                    frame = frame.copy()
                    t = local / FPS
                    transition = board.transition_frames
                    if previous is not None and transition > 1 and local < transition:
                        p = ease(local / (transition - 1))
                        frame = (previous * (1 - p) + frame * p).astype(np.uint8)
                    if shown_product:
                        alpha = ease((t - 0.3) / PRODUCT_FADE) if product_fade else 1
                        if alpha > 0:
                            _blend(frame, shown_product[0], shown_product[1], shown_product[2], alpha)
                    for layer, x, y, appear in titles:
                        if t >= appear:
                            alpha = ease((t - appear) / TITLE_FADE)
                            _blend(frame, layer, x, round(y + 10 * (1 - alpha)), alpha)
                    absolute = (start + local) / FPS
                    if absolute >= duration - FADE_OUT:
                        p = ease((absolute - duration + FADE_OUT) / FADE_OUT)
                        frame = (frame * (1 - p) + bg * p).astype(np.uint8)
                    writer.stdin.write(frame.tobytes())
                    written += 1
                    if written % 15 == 0:
                        job.update(72 + 18 * written / frames, f"Ghép khung hình {written}/{frames}")
                previous = last
            writer.stdin.close()
            status = writer.wait()
        except BaseException:
            writer.kill()
            raise
    if status or written != frames:
        raise ValueError(f"Ghép hình vẽ tay chưa hoàn tất ({written}/{frames} khung hình).")

    job.start_step("mix-audio")
    job.update(91, "Trộn giọng đọc" + (" và nhạc nền" if music else ""))
    output = directory / "video.mp4"
    voice_chain = f"{VOICE_FILTER},aresample=48000,apad,atrim=0:{duration}"
    encode = ["-c:v", "copy", "-c:a", media.audio_encoder(), "-b:a", "192k", "-ar", "48000", "-ac", "2",
              "-t", f"{duration:.6f}", "-movflags", "+faststart", output]
    if music:
        fade = min(1.7, duration / 3)
        graph = (f"[1:a]{voice_chain},asplit=2[voice][key];[2:a]volume={board.music_volume},aresample=48000,"
                 f"atrim=0:{duration},afade=t=out:st={duration - fade}:d={fade}[music];"
                 "[music][key]sidechaincompress=threshold=0.018:ratio=4:attack=18:release=300[duck];"
                 "[voice][duck]amix=inputs=2:duration=first:normalize=0,"
                 "alimiter=limit=0.891:level=false:latency=true[a]")
        media.run_ff(["-i", visuals, "-i", voice, "-stream_loop", "-1", "-i", music, "-filter_complex", graph,
                      "-map", "0:v:0", "-map", "[a]", *encode], job=job)
    else:
        media.run_ff(["-i", visuals, "-i", voice, "-map", "0:v:0", "-map", "1:a:0", "-af", voice_chain,
                      *encode], job=job)
    info = media.probe(output)
    if (info["width"], info["height"]) != size or abs(info["duration"] - duration) > 0.1:
        raise ValueError("Video vẽ tay không đúng kích thước hoặc thời lượng.")
    job.start_step("subtitles")
    (directory / "captions.srt").write_text(srt(project, duration), encoding="utf-8")
    for path in work.iterdir():
        path.unlink()
    work.rmdir()
    return {
        "id": job.id, "kind": "whiteboard", "filename": "video.mp4", "duration": round(duration, 2),
        "width": size[0], "height": size[1], "fps": FPS, "preview": False,
        "source_revision": project.content_revision, "case": board.case, "aspect": board.aspect,
        "scene_count": len(prepared), "clip_cache_hits": hits[0], "clip_cache_misses": len(prepared) - hits[0],
        "encoder": "libx264", "hardware_encoder": False, "whiteboard_version": VERSION,
        "size": output.stat().st_size, "created_at": now(),
    }


def preview(project, scene_id):
    """PNG of a scene's finished drawing with titles, product and region outlines."""
    board = project.whiteboard
    scene = next((item for item in board.scenes if item.id == scene_id), None)
    if scene is None:
        raise FileNotFoundError(scene_id)
    if not scene.image_id:
        raise ValueError("Cảnh chưa có tranh")
    index = board.scenes.index(scene)
    end = board.scenes[index + 1].start if index + 1 < len(board.scenes) else max(scene.end, scene.start + 3)
    frames = max(round(MIN_SCENE * FPS), round((end - scene.start) * FPS))
    product_image = None
    if scene.product and board.product_id:
        product_image = store.asset_path(project.id, store.get_asset(project, board.product_id))
    canvas, annotation, layout = prepare(project, scene, frames, product_image)
    frame = np.array(canvas)
    if layout["product"] and product_image is not None:
        layer, x, y = product_layer(product_image, layout["product"])
        _blend(frame, layer, x, y, 1)
    for layer, x, y, _ in title_layers(scene, layout, frames / FPS):
        _blend(frame, layer, x, y, 1)
    image = Image.fromarray(frame)
    draw = ImageDraw.Draw(image)
    for element in annotation["elements"]:
        r = element["region"]
        draw.rectangle((r["x"], r["y"], r["x"] + r["width"] - 1, r["y"] + r["height"] - 1),
                       outline=(77, 140, 255), width=3)
        draw.text((r["x"] + 8, r["y"] + 6), str(element["sequence"]), fill=(77, 140, 255), font=_font(28))
    image.thumbnail((960, 960))
    return image
