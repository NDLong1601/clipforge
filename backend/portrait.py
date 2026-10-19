"""Portrait whiteboard framing for a finished export.

Ported from the presentation stage of the srt-whiteboard-animation-custom kit:
animated purple grid, voice-driven chibi narrator at lower left, the finished
video contained in the middle, a yellow hook above and optional Relaxed music.
The source export is never re-rendered; its MP4 is framed as-is.
"""

from __future__ import annotations

import math
import os
import subprocess
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import font_manager, media, processes, store
from .store import now

VERSION = 1
ASSETS = Path(__file__).resolve().parent / "assets" / "portrait"
WIDTH, HEIGHT, FPS = 1080, 1920, 30
CASES = {"nam": "Người kể nam", "nu": "Người kể nữ"}
PLAYBACK_RATES = (1.0, 1.1, 1.2)
OUTPUT = "portrait.mp4"
COVER = "portrait-cover.jpg"
# Boxes keep the finished video whole: 16:9 matches the approved whiteboard
# portrait layout; taller sources get a narrower box below the hook.
SOURCE_BOXES = {
    "16:9": (0, 620, 1080, 608),
    "1:1": (140, 560, 800, 800),
    "9:16": (240, 540, 600, 1066),
}
NARRATORS = {
    "nam": {
        "image": "narrator-nam.png",
        # Feet anchors measured on the approved male sheet.
        "anchors": [[0.52631579, 0.95374801], [0.46251994, 0.95374801],
                    [0.52631579, 0.94417863], [0.46251994, 0.94577352]],
    },
    "nu": {"image": "narrator-nu.png", "anchors": None},
}
NARRATOR_RECT = (0, 1070, 520, 800)
NARRATOR_FPS = 15
ROLES = {"idle": 0, "talk": 1, "point": 2, "present": 3}
SPEECH_THRESHOLD = 0.02
MUSIC_LOUDNESS, MUSIC_VOLUME = -26, 0.72
HOOK = {"font_size": 48, "max_width": 960, "center_y": 425, "line_height": 82,
        "color": "#FFFF00", "outline": "#000000"}
MAX_HOOK_LINES = 3
MAX_HOOK_CHARS = 60


def options(payload, source_export):
    """Validate the request; returns normalized options or raises ValueError."""
    case = payload.get("case", "nam")
    if case not in CASES:
        raise ValueError("Chọn người kể nam hoặc nữ.")
    lines = payload.get("hook_lines", [])
    if not isinstance(lines, list) or len(lines) > MAX_HOOK_LINES:
        raise ValueError(f"Hook có tối đa {MAX_HOOK_LINES} dòng.")
    lines = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if any(len(line) > MAX_HOOK_CHARS for line in lines):
        raise ValueError(f"Mỗi dòng hook tối đa {MAX_HOOK_CHARS} ký tự.")
    rate = payload.get("playback_rate", 1.1)
    if isinstance(rate, bool) or rate not in PLAYBACK_RATES:
        raise ValueError("Tốc độ xuất chỉ nhận 1.0, 1.1 hoặc 1.2.")
    music = payload.get("music", True)
    if not isinstance(music, bool):
        raise ValueError("music phải là giá trị boolean.")
    if not isinstance(source_export, dict) or source_export.get("kind") == "portrait":
        raise ValueError("Chọn một bản xuất video thường để ghép bản dọc.")
    return {"case": case, "hook_lines": lines, "playback_rate": float(rate), "music": music}


def source_box(width, height):
    ratio = width / height
    aspect = min(SOURCE_BOXES, key=lambda key: abs(Fraction(key.replace(":", "/")) - Fraction(ratio).limit_denominator(100)))
    return SOURCE_BOXES[aspect]


def _video_info(path):
    with av.open(str(path)) as container:
        video = next(iter(container.streams.video), None)
        audio = next(iter(container.streams.audio), None)
        if video is None:
            raise ValueError("Bản xuất nguồn không có hình.")
        if audio is None:
            raise ValueError("Bản xuất nguồn không có âm thanh để điều khiển người kể.")
        if video.duration is not None and video.time_base:
            duration = Fraction(video.duration) * video.time_base
        else:
            duration = Fraction(container.duration or 0, av.time_base)
        if duration <= 0:
            raise ValueError("Không đọc được thời lượng bản xuất nguồn.")
        return {"width": video.width, "height": video.height, "duration": duration,
                "frames": math.ceil(duration * FPS)}


def _audio_samples(path, job):
    # 8 kHz mono float is enough to tell speech from silence for cel switching.
    result = processes.run(
        [media.ffmpeg(), "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1",
         "-ar", "8000", "-f", "f32le", "pipe:1"],
        timeout=600, capture_output=True, job=job,
    )
    if result.returncode:
        raise ValueError("Không đọc được âm thanh bản xuất nguồn.")
    return np.frombuffer(result.stdout, dtype=np.float32)


def narrator_states(case):
    """Cut each 2x2 cel, align it on the feet and scale it into the narrator rect."""
    import cv2

    settings = NARRATORS[case]
    sheet = Image.open(ASSETS / settings["image"]).convert("RGBA")
    columns = rows = 2
    cw, ch = sheet.width // columns, sheet.height // rows
    rw, rh = NARRATOR_RECT[2:]
    target_height, figure_x, floor = rh * 0.9, rw * 0.356, rh * 0.9875
    states = []
    for index in range(columns * rows):
        x, y = index % columns, index // columns
        cell = sheet.crop((x * cw, y * ch, (x + 1) * cw, (y + 1) * ch))
        alpha = np.array(cell.getchannel("A"))
        count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 48).astype(np.uint8))
        if count < 2:
            raise ValueError(f"Ảnh người kể thiếu tư thế {index}.")
        label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        left, top, width, height = map(int, stats[label, :4])
        keep = cv2.dilate((labels == label).astype(np.uint8), np.ones((3, 3), np.uint8))
        cell.putalpha(Image.fromarray(alpha * keep))
        if settings["anchors"]:
            anchor_x, anchor_y = settings["anchors"][index][0] * cw, settings["anchors"][index][1] * ch
        else:
            foot = alpha[top + round(height * 0.83):top + height, left:left + width]
            _, xs = np.nonzero(foot > 48)
            anchor_x = left + (float(xs.mean()) if len(xs) else width / 2)
            anchor_y = top + height
        scale = min(target_height / height, figure_x / max(1, anchor_x - left),
                    (rw - figure_x) / max(1, left + width - anchor_x))
        piece = cell.crop((left, top, left + width, top + height)).resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        canvas.alpha_composite(piece, (round(figure_x + (left - anchor_x) * scale),
                                       round(floor + (top - anchor_y) * scale)))
        states.append(canvas)
    return states


def narrator_frames(states, samples, duration):
    """Yield RGBA frames: talk while the voice has energy, gestures on a 12 s cycle."""
    rw, rh = NARRATOR_RECT[2:]
    motion = 2
    for frame in range(math.ceil(duration * NARRATOR_FPS)):
        t = frame / NARRATOR_FPS
        a, b = max(0, int((t - 0.075) * 8000)), min(len(samples), int((t + 0.1) * 8000))
        speaking = b > a and float(np.sqrt(np.mean(samples[a:b] ** 2))) > SPEECH_THRESHOLD
        role = "idle"
        if speaking:
            role = "point" if 4 <= t % 12 < 6 else "present" if 8 <= t % 12 < 10 else "talk"
        dx = round(motion * 0.6 * math.sin(t * 1.4))
        dy = round(motion * math.sin(t * 3.4) + (motion * 0.9 * math.sin(t * 10) if speaking else 0))
        canvas = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        canvas.alpha_composite(states[ROLES[role]], (dx, dy))
        yield canvas.tobytes()


def _write_narrator(path, frames, job):
    rw, rh = NARRATOR_RECT[2:]
    arguments = [media.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                 "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{rw}x{rh}", "-r", str(NARRATOR_FPS),
                 "-i", "pipe:0", "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(path)]
    with processes.command(arguments, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, popen_factory=subprocess.Popen) as process:
        try:
            for index, data in enumerate(frames):
                if index % 15 == 0:
                    job.check()
                process.stdin.write(data)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
        _, stderr = process.communicate(timeout=300)
        if process.returncode:
            raise ValueError("Không dựng được người kể: " + stderr.decode("utf-8", "replace")[-600:])


def _hook_font():
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguibl.ttf"
    if windows.is_file():
        return str(windows)
    path, _, _ = font_manager.resolve("Segoe UI", bold=True)
    if path is None:
        raise ValueError("Không tìm thấy font đậm để vẽ hook.")
    return str(path)


def make_hook(lines, path):
    if not lines:
        return None
    font_file = _hook_font()
    size = HOOK["font_size"]
    while size > 8:
        font = ImageFont.truetype(font_file, size)
        if max(font.getlength(line) for line in lines) <= HOOK["max_width"]:
            break
        size -= 1
    gap, cy = HOOK["line_height"], HOOK["center_y"]
    top = cy - (len(lines) - 1) * gap / 2
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    for index, line in enumerate(lines):
        draw.text((WIDTH / 2, top + index * gap), line, font=font, fill=HOOK["color"],
                  anchor="mm", stroke_width=7, stroke_fill=HOOK["color"])
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((WIDTH / 2, top + index * gap), line, font=font, fill=HOOK["color"],
                  anchor="mm", stroke_width=3, stroke_fill=HOOK["outline"])
    image.save(path)
    return path


def filter_graph(box, rate, frames, duration, hook, music):
    """Inputs: 0 grid background, 1 source, 2 narrator, [3 hook], [last music]."""
    x, y, w, h = box
    filters = [
        # The approved grid is landscape; rotate it, then cover the portrait canvas.
        f"[0:v]transpose=clock,scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,setpts=PTS-STARTPTS[bg]",
        f"[1:v]fps={FPS},scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={1 / FPS}[main]",
        f"[bg][main]overlay={x}:{y}:shortest=1[layer0]",
        f"[layer0][2:v]overlay={NARRATOR_RECT[0]}:{NARRATOR_RECT[1]}:eof_action=repeat[layer1]",
    ]
    layer, music_index = 1, 3
    if hook:
        filters.append(f"[layer1][3:v]overlay=0:0:eof_action=repeat[layer2]")
        layer, music_index = 2, 4
    retime = (f"setpts=(PTS-STARTPTS)/{rate},fps={FPS},tpad=stop_mode=clone:stop_duration=1,"
              f"trim=end_frame={frames}," if rate != 1 else "")
    filters.append(f"[layer{layer}]{retime}format=yuv420p[v]")
    tempo = f"atempo={rate}," if rate != 1 else ""
    voice = f"[1:a]{tempo}aresample=48000,apad,atrim=0:{duration}"
    limiter = "alimiter=limit=0.891:level=false:latency=true[a]"
    if music:
        fade = min(1, duration / 3)
        filters += [
            f"{voice},asplit=2[voice][key]",
            f"[{music_index}:a]aresample=48000,loudnorm=I={MUSIC_LOUDNESS}:TP=-5:LRA=7,"
            f"volume={MUSIC_VOLUME},afade=t=in:st=0:d={min(0.6, fade)},"
            f"afade=t=out:st={duration - fade}:d={fade},atrim=0:{duration}[music]",
            # Duck the music under the narration, then keep the peak below -1 dBTP.
            "[music][key]sidechaincompress=threshold=0.030:ratio=2.5:attack=35:release=320[duck]",
            f"[voice][duck]amix=inputs=2:duration=first:normalize=0,{limiter}",
        ]
    else:
        filters.append(f"{voice},{limiter}")
    return ";".join(filters)


def _encoder_options():
    from . import render

    name, hardware, flags = render.video_encoder()
    if hardware:
        return name, True, ["-c:v", name, *flags]
    return "libx264", False, ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-tune", "animation"]


def render(project, job, payload):
    job.start_step("preflight")
    source_export = next((item for item in project.exports if item.get("id") == payload.get("export_id")), None)
    if source_export is None:
        raise ValueError("Không tìm thấy bản xuất nguồn. Dựng video trước khi ghép bản dọc.")
    settings = options(payload, source_export)
    source_dir = store.project_dir(project.id) / "exports" / source_export["id"]
    source = source_dir / str(source_export.get("filename", ""))
    if source.parent != source_dir or not source.is_file():
        raise ValueError("File bản xuất nguồn đã bị xóa. Dựng lại video rồi thử lại.")
    info = _video_info(source)
    rate = settings["playback_rate"]
    frames = math.ceil(Fraction(info["frames"]) / Fraction(str(rate)))
    duration = frames / FPS
    directory = store.project_dir(project.id) / "exports" / job.id
    work = directory / "work"
    work.mkdir(parents=True, exist_ok=True)

    job.start_step("source-audio")
    job.update(10, "Đọc giọng nói để điều khiển người kể")
    samples = _audio_samples(source, job)
    job.start_step("encode-video")
    job.update(20, "Dựng người kể chuyện")
    narrator = work / "narrator.mov"
    _write_narrator(narrator, narrator_frames(narrator_states(settings["case"]), samples, float(info["duration"])), job)
    hook = make_hook(settings["hook_lines"], work / "hook.png")

    job.update(45, "Ghép nền, người kể, hook và nhạc")
    box = source_box(info["width"], info["height"])
    graph = filter_graph(box, rate, frames, duration, hook, settings["music"])
    inputs = ["-stream_loop", "-1", "-i", ASSETS / "grid.webm", "-i", source, "-i", narrator]
    if hook:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", hook]
    if settings["music"]:
        inputs += ["-stream_loop", "-1", "-i", ASSETS / "relaxed.mp3"]
    audio = ["-c:a", media.audio_encoder(), "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    output = directory / OUTPUT
    encoder, hardware, encode = _encoder_options()
    common = [*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
    tail = ["-pix_fmt", "yuv420p", "-r", str(FPS), *audio, "-t", str(duration), "-movflags", "+faststart", output]
    job.start_step("mux")
    try:
        media.run_ff([*common, *encode, *tail], job=job)
    except ValueError:
        if not hardware:
            raise
        # Same fallback policy as normal exports: a broken GPU encoder must not block the job.
        job.cpu_fallback()
        encoder, hardware = "libx264", False
        media.run_ff([*common, "-c:v", "libx264", "-preset", "fast", "-crf", "18", *tail], job=job)

    result = media.probe(output)
    if (result["width"], result["height"]) != (WIDTH, HEIGHT) or abs(result["duration"] - duration) > 0.1:
        raise ValueError("Bản dọc không đúng kích thước hoặc thời lượng.")
    media.run_ff(["-ss", f"{duration * 0.09:.3f}", "-i", output, "-frames:v", "1", "-q:v", "2", directory / COVER], job=job)
    for path in work.iterdir():
        path.unlink()
    work.rmdir()
    return {
        "id": job.id, "kind": "portrait", "filename": OUTPUT, "cover": COVER,
        "duration": round(duration, 2), "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "preview": False, "source_export_id": source_export["id"],
        "source_revision": source_export.get("source_revision"),
        "case": settings["case"], "hook": settings["hook_lines"], "music": settings["music"],
        "playback_rate": rate, "encoder": encoder, "hardware_encoder": hardware,
        "portrait_version": VERSION, "size": output.stat().st_size, "created_at": now(),
    }
