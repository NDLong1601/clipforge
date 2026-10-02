import math, textwrap, shutil, json, hashlib, os, subprocess, threading, re
import av
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .store import project_dir, get_asset, asset_path, now
from . import store
from .models import Cue
from .media import run_ff, dimensions, probe, proxy_for_preview, ffmpeg
from .planner import validate_timeline, caption_groups
from .preflight import preflight
from .audio_cache import normalized_audio
from .font_manager import resolve
from .timeline_math import (
    clip_starts,
    overlap_before,
    timeline_duration,
    caption_windows,
)
from .template_content import layer_text
from . import sound_effects

RENDER_VERSION = "render-v5-frame-clock"


def _frame_project(project):
    """Round absolute boundaries, so fractional clip lengths never accumulate drift."""
    result = project.model_copy(deep=True)
    starts = clip_starts(project.clips)
    ends = [
        round((start + clip.duration) * 30)
        for start, clip in zip(starts, project.clips)
    ]
    for index, clip in enumerate(result.clips):
        first = round(starts[index] * 30)
        clip.duration = (ends[index] - first) / 30
        if index and clip.transition == "crossfade":
            clip.transition_duration = (ends[index - 1] - first) / 30
    return result


def font(size, family="Arial"):
    path, _, _ = resolve(family, bold=True)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


_ENCODER_LOCK = threading.Lock()
_VIDEO_ENCODER = None


def video_encoder():
    """Enable hardware encoding only after a real encode succeeds on this machine."""
    global _VIDEO_ENCODER
    if _VIDEO_ENCODER is not None:
        return _VIDEO_ENCODER
    with _ENCODER_LOCK:
        if _VIDEO_ENCODER is not None:
            return _VIDEO_ENCODER
        executable = ffmpeg()
        options = {
            "h264_nvenc": ["-preset", "p4", "-cq", "24", "-b:v", "0"],
            "h264_qsv": ["-preset", "veryfast", "-global_quality", "26"],
            "h264_amf": ["-quality", "speed", "-qp_i", "24", "-qp_p", "24"],
        }
        selected = ("libx264", False, [])
        for encoder, flags in options.items():
            try:
                result = subprocess.run(
                    [
                        executable,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=c=black:s=64x64:r=1",
                        "-frames:v",
                        "1",
                        "-c:v",
                        encoder,
                        *flags,
                        "-f",
                        "null",
                        "-",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if result.returncode == 0:
                    selected = (encoder, True, flags)
                    break
            except (OSError, subprocess.TimeoutExpired):
                continue
        _VIDEO_ENCODER = selected
        return selected


def _video_encode_options(preview, encoder):
    name, hardware, flags = encoder
    if hardware:
        return ["-c:v", name, *flags]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24" if preview else "20",
        "-threads",
        "2",
    ]


def _hardware_encoder_error(exc):
    detail = str(exc).casefold()
    return any(
        marker in detail
        for marker in (
            "hardware device",
            "device unavailable",
            "no nvenc capable devices",
            "no capable devices",
            "cannot load nvcuda",
            "cannot load libcuda",
            "cannot load libnvidia-encode",
            "error while opening encoder",
        )
    ) or bool(
        re.search(
            r"(?:nvenc|qsv|amf|mfx).*(?:failed|error|unsupported|unavailable)|"
            r"(?:failed|error|cannot|unsupported).*(?:nvenc|qsv|amf|mfx|cuda)",
            detail,
        )
    )


def _run_video_encode(args, output, preview, encoder, cwd, job):
    """Retry a hardware-device failure once on CPU, retaining the chosen encoder."""
    global _VIDEO_ENCODER
    try:
        run_ff(
            [*args, *_video_encode_options(preview, encoder), output], cwd=cwd, job=job
        )
    except ValueError as exc:
        if job:
            job.check()
        if not encoder[1] or not _hardware_encoder_error(exc):
            raise
        target = Path(output)
        if not target.is_absolute():
            target = Path(cwd) / target
        target.unlink(missing_ok=True)
        encoder[:] = ["libx264", False, []]
        with _ENCODER_LOCK:
            _VIDEO_ENCODER = tuple(encoder)
        if job:
            job.update(
                job.progress, "Encoder phần cứng không khả dụng; tiếp tục bằng CPU"
            )
        run_ff(
            [*args, *_video_encode_options(preview, encoder), output], cwd=cwd, job=job
        )


def _font_signature(layers, caption_family):
    signatures = []
    families = {caption_family} | {
        layer.font_family for layer in layers if layer.kind == "text"
    }
    for family in sorted(families):
        path, actual, found = resolve(family)
        stat = path.stat() if path and path.is_file() else None
        signatures.append(
            {
                "requested": family,
                "resolved": actual,
                "found": found,
                "path": str(path) if path else "",
                "size": stat.st_size if stat else 0,
                "mtime": stat.st_mtime_ns if stat else 0,
            }
        )
    return signatures


def wrap_text(draw, text, fnt, width):
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            new = (line + " " + word).strip()
            if draw.textbbox((0, 0), new, font=fnt)[2] > width and line:
                lines.append(line)
                line = word
            else:
                line = new
        lines.append(line)
    return "\n".join(lines)


def layer_image(p, layer, clip, width, height, path):
    lw, lh = max(2, round(layer.w * width)), max(2, round(layer.h * height))
    im = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    if layer.kind in ["rect", "circle"]:
        fn = draw.rectangle if layer.kind == "rect" else draw.ellipse
        fn((0, 0, lw - 1, lh - 1), fill=layer.color)
    elif layer.kind == "image":
        if not layer.asset_id:
            return None
        a = get_asset(p, layer.asset_id)
        with Image.open(asset_path(p.id, a)) as src:
            src = ImageOps.contain(src.convert("RGBA"), (lw, lh))
            im.alpha_composite(src, ((lw - src.width) // 2, (lh - src.height) // 2))
    else:
        text = layer_text(p, layer, clip)
        size = max(10, round(layer.size * width / 1080))
        for fs in range(size, 9, -1):
            fnt = font(fs, layer.font_family)
            wrapped = wrap_text(draw, text, fnt, lw - 8)
            box = draw.multiline_textbbox((0, 0), wrapped, font=fnt, spacing=fs * 0.18)
            if box[3] - box[1] <= lh - 4:
                break
        draw.multiline_text(
            (2, -box[1] + 2),
            wrapped,
            font=fnt,
            fill=layer.color,
            spacing=fs * 0.18,
            stroke_width=max(0, round(width / 1080)),
            stroke_fill=layer.background,
        )
    if layer.opacity < 1:
        im.putalpha(im.getchannel("A").point(lambda x: int(x * layer.opacity)))
    im.save(path)
    return path


def ass_time(t):
    cs = round(max(0, t) * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def srt_time(t):
    ms = round(max(0, t) * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def safe_ass(text):
    return (
        text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    )


def ass_color(color):
    return "&H00" + color[5:7] + color[3:5] + color[1:3]


def subtitles(p, width, height, directory):
    overrides = []
    for clip, (start, end) in zip(p.clips, caption_windows(p.clips)):
        if clip.caption and end > start:
            overrides.append(Cue(start=start, end=end, text=clip.caption))
    base = []
    for c in p.cues:
        ranges = [(c.start, c.end)]
        for override in overrides:
            ranges = [
                part
                for start, end in ranges
                for part in [
                    (start, min(end, override.start)),
                    (max(start, override.end), end),
                ]
                if part[1] > part[0]
            ]
        base.extend(Cue(start=s, end=e, text=c.text) for s, e in ranges if e > s)
    base.extend(overrides)
    base.sort(key=lambda c: c.start)
    style = p.template.caption
    groups = []
    pending = []
    boundaries = {c.start for c in overrides} | {c.end for c in overrides}
    for c in base:
        if pending and c.start in boundaries:
            groups.extend(caption_groups(pending, style.words_per_line))
            pending = []
        pending.append(c)
    groups.extend(caption_groups(pending, style.words_per_line))
    srt = []
    events = []
    for i, group in enumerate(groups):
        start, end = group[0].start, group[-1].end
        text = " ".join(w.text for w in group)
        srt.append(f"{i+1}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
        if style.karaoke:
            text = " ".join(
                "{\\kf"
                + str(max(1, round((w.end - w.start) * 100)))
                + "}"
                + safe_ass(w.text)
                for w in group
            )
        else:
            text = safe_ass(text)
        events.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}"
        )
    safe_bottom = {"standard": 0.06, "reels": 0.16, "cinematic": 0.1}.get(
        style.safe_area, 0.06
    )
    size = round(style.font_size * width / 1080)
    margin = round(max(style.bottom, safe_bottom) * height)
    _, font_family, _ = resolve(style.font_family)
    rgb = [int(style.color[i : i + 2], 16) for i in (1, 3, 5)]
    dark = sum(rgb) < 360
    outline = "&H00FFFFFF" if dark else "&H00101010"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family},{size},{ass_color(style.highlight if style.karaoke else style.color)},{ass_color(style.color)},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{max(1,round(width/540))},{0 if dark else 1},2,{round(width*.06)},{round(width*.06)},{margin},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    (directory / "captions.ass").write_text(
        header + "\n".join(events), encoding="utf-8-sig"
    )
    (directory / "captions.srt").write_text("\n".join(srt), encoding="utf-8-sig")


def _layer_list(p, clip, index):
    layers = list(p.template.layers)
    if p.mode == "template" and p.template.slot_layers:
        layers += p.template.slot_layers[index % len(p.template.slot_layers)]
    return layers + list(clip.layers)


def _asset_file_signature(p, asset_ids):
    values = []
    for asset_id in sorted(set(asset_ids)):
        asset = next((item for item in p.assets if item.id == asset_id), None)
        if not asset:
            continue
        path = asset_path(p.id, asset)
        try:
            stat = path.stat()
            identity = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            identity = (0, 0)
        values.append((asset.id, asset.filename, *identity))
    return values


def _clip_cache_path(
    p,
    clip,
    asset,
    index,
    layers,
    source,
    width,
    height,
    vw,
    vh,
    vx,
    vy,
    preview,
    encoder,
):
    original = asset_path(p.id, asset)
    stat = original.stat()
    layer_asset_ids = [
        layer.asset_id for layer in layers if layer.kind == "image" and layer.asset_id
    ]
    payload = {
        "version": RENDER_VERSION,
        "asset": (asset.id, asset.filename, stat.st_size, stat.st_mtime_ns),
        "source_text_mode": p.source_text_mode,
        "source_text_regions": [
            region.model_dump() for region in source_text_regions(clip, asset)
        ],
        "layer_assets": _asset_file_signature(p, layer_asset_ids),
        "clip": clip.model_dump(exclude={"id", "locked"}),
        "project_name": p.name,
        "aspect": p.aspect,
        "resolution": ("720" if preview else p.resolution),
        "resolved_text": [
            layer_text(p, layer, clip) for layer in layers if layer.kind == "text"
        ],
        "canvas": (width, height, vw, vh, vx, vy),
        "background": p.template.background,
        "viewport": p.template.viewport.model_dump(),
        "layers": [layer.model_dump() for layer in layers],
        "fonts": _font_signature(layers, "Arial"),
        "preview": preview,
        "encoder": encoder[0],
        "proxy": str(source) != str(original),
    }
    key = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    cache = store.ROOT / "cache" / "rendered-clips" / f"{key}.mp4"
    return key, cache


def source_text_regions(clip, asset):
    if clip.text_regions_override or clip.text_regions:
        return clip.text_regions
    scene = next((scene for scene in asset.scenes if scene.id == clip.scene_id), None)
    return scene.text_regions if scene else []


def source_text_filters(p, clip, asset):
    """Mask source coordinates before crop/scale, leaving generated captions intact."""
    mode = p.source_text_mode if clip.text_mode == "inherit" else clip.text_mode
    filters = []
    last = "source_base"
    for index, region in enumerate(
        source_text_regions(clip, asset) if mode != "off" else []
    ):
        label = f"clean{index}"
        if mode == "cover":
            filters.append(
                f"[{last}]drawbox=x=iw*{region.x}:y=ih*{region.y}:w=iw*{region.w}:h=ih*{region.h}:color=0x111111:t=fill[{label}]"
            )
        else:
            filters.append(f"[{last}]split=2[keep{index}][patch{index}]")
            filters.append(
                f"[patch{index}]crop=w=max(2\\,trunc(iw*{region.w}/2)*2):h=max(2\\,trunc(ih*{region.h}/2)*2):"
                f"x=min(iw-ow\\,trunc(iw*{region.x}/2)*2):y=min(ih-oh\\,trunc(ih*{region.y}/2)*2),gblur=sigma=18:steps=2[blur{index}]"
            )
            filters.append(
                f"[keep{index}][blur{index}]overlay=x=main_w*{region.x}:y=main_h*{region.y}:shortest=1[{label}]"
            )
        last = label
    return filters, last


def _render_clip(
    p,
    clip,
    asset,
    index,
    layers,
    source,
    d,
    width,
    height,
    vw,
    vh,
    vx,
    vy,
    preview,
    encoder,
    job,
):
    output = d / f"clip_{index:03d}.mp4"
    key, cache = _clip_cache_path(
        p,
        clip,
        asset,
        index,
        layers,
        source,
        width,
        height,
        vw,
        vh,
        vx,
        vy,
        preview,
        encoder,
    )
    if cache.is_file() and cache.stat().st_size > 0:
        try:
            cached = probe(cache)
            if (
                cached.get("media") == "video"
                and cached.get("width") == width
                and cached.get("height") == height
                and abs(cached.get("duration", 0) - clip.duration) <= 0.002
            ):
                with av.open(str(cache)) as container:
                    stream = container.streams.video[0]
                    valid = (
                        stream.frames == round(clip.duration * 30)
                        and stream.average_rate == 30
                    )
                if valid:
                    shutil.copy2(cache, output)
                    return output, True
        except (OSError, ValueError):
            pass
        cache.unlink(missing_ok=True)
    args = []
    if asset.media == "image":
        args += ["-loop", "1", "-framerate", "30", "-i", source]
    else:
        available = asset.duration - clip.source_start
        if clip.scene_id:
            scene = next(
                (item for item in asset.scenes if item.id == clip.scene_id), None
            )
            if scene and clip.source_start < scene.end:
                available = min(available, scene.end - clip.source_start)
        args += [
            "-ss",
            clip.source_start,
            "-t",
            min(available, clip.duration * clip.speed),
            "-i",
            source,
        ]
    if clip.fit == "cover":
        scale = f"scale={vw}:{vh}:force_original_aspect_ratio=increase,crop={vw}:{vh}:(iw-ow)*{clip.crop_x}:(ih-oh)*{clip.crop_y}"
    else:
        scale = f"scale={vw}:{vh}:force_original_aspect_ratio=decrease,pad={vw}:{vh}:(ow-iw)/2:(oh-ih)/2:color={p.template.background}"
    filters = [
        f"[0:v]setpts=(PTS-STARTPTS)/{clip.speed},fps=30:start_time=0,setsar=1[source_base]"
    ]
    cleanup, source_label = source_text_filters(p, clip, asset)
    filters.extend(cleanup)
    filters.append(
        f"[{source_label}]{scale},setsar=1,tpad=stop_mode=clone:stop_duration={clip.duration},trim=duration={clip.duration},pad={width}:{height}:{vx}:{vy}:color={p.template.background}[base]"
    )
    last = "base"
    input_index = 1
    for layer_index, layer in enumerate(layers):
        path = d / f"layer_{index}_{layer_index}.png"
        if not layer_image(p, layer, clip, width, height, path):
            continue
        args += ["-loop", "1", "-framerate", "30", "-i", path]
        plane = f"plane{layer_index}"
        label = f"over{layer_index}"
        filters.append(
            f"[{input_index}:v]format=rgba"
            + (",fade=t=in:st=0:d=0.25:alpha=1" if layer.animation == "fade" else "")
            + f"[{plane}]"
        )
        x = str(round(layer.x * width))
        y = round(layer.y * height)
        if layer.animation == "slide":
            x = f"'{x}-{round(layer.w*width)}*max(0,1-t/0.25)'"
        filters.append(f"[{last}][{plane}]overlay=x={x}:y={y}:shortest=1[{label}]")
        last = label
        input_index += 1
    end = "format=yuv420p"
    if clip.transition == "fade":
        fade = min(0.18, clip.duration / 4)
        end += f",fade=t=in:d={fade},fade=t=out:st={clip.duration-fade}:d={fade}"
    frames = round(clip.duration * 30)
    filters.append(
        f"[{last}]{end},tpad=stop_mode=clone:stop_duration=1,trim=end_frame={frames},settb=1/30,setpts=N[out]"
    )
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[out]",
        "-an",
        "-frames:v",
        frames,
        "-r",
        "30",
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        "15360",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    _run_video_encode(args, output, preview, encoder, d, job)
    probe(output)
    # A fallback must publish under the actual CPU encoder's cache identity.
    key, cache = _clip_cache_path(
        p,
        clip,
        asset,
        index,
        layers,
        source,
        width,
        height,
        vw,
        vh,
        vx,
        vy,
        preview,
        encoder,
    )
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_name(f'{key}.{getattr(job,"id","render")}.tmp.mp4')
        shutil.copy2(output, temporary)
        os.replace(temporary, cache)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
    return output, False


def _join_video(p, d, job, duration, encoder, preview):
    clips = [d / f"clip_{index:03d}.mp4" for index in range(len(p.clips))]
    if not any(overlap_before(p.clips, index) for index in range(1, len(p.clips))):
        (d / "list.txt").write_text(
            "\n".join(f"file 'clip_{index:03d}.mp4'" for index in range(len(clips))),
            encoding="utf-8",
        )
        run_ff(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                "list.txt",
                "-c",
                "copy",
                "joined.mp4",
            ],
            cwd=d,
            job=job,
        )
        return d / "joined.mp4"
    args = []
    filters = []
    for index, path in enumerate(clips):
        args += ["-i", path]
        frames = round(p.clips[index].duration * 30)
        filters.append(
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1,"
            f"fps=30:start_time=0,trim=end_frame={frames},settb=AVTB,format=yuv420p[v{index}]"
        )
    last = "v0"
    elapsed = p.clips[0].duration
    for index, clip in enumerate(p.clips[1:], 1):
        overlap = overlap_before(p.clips, index)
        label = f"j{index}"
        if overlap:
            filters.append(
                f"[{last}][v{index}]xfade=transition=fade:duration={overlap}:offset={elapsed-overlap}[{label}]"
            )
            elapsed += clip.duration - overlap
        else:
            filters.append(f"[{last}][v{index}]concat=n=2:v=1:a=0[{label}]")
            elapsed += clip.duration
        last = label
    frames = round(duration * 30)
    filters.append(
        f"[{last}]tpad=stop_mode=clone:stop_duration=1,fps=30:start_time=0,trim=end_frame={frames},settb=1/30,setpts=N[joined_clock]"
    )
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[joined_clock]",
        "-an",
        "-frames:v",
        frames,
        "-r",
        "30",
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        "15360",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    _run_video_encode(args, "joined.mp4", preview, encoder, d, job)
    return d / "joined.mp4"


def _atempo(speed):
    values = []
    remaining = float(speed)
    while remaining < 0.5:
        values.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2:
        values.append("atempo=2")
        remaining /= 2
    values.append(f"atempo={remaining:.6f}")
    return ",".join(values)


def _source_audio_track(p, d, job, duration):
    if p.source_volume <= 0:
        return None
    paths = []
    for index, clip in enumerate(p.clips):
        job.check()
        asset = get_asset(p, clip.asset_id)
        output = d / f"source_audio_{index:03d}.wav"
        if asset.media == "video" and asset.has_audio:
            available = asset.duration - clip.source_start
            if clip.scene_id:
                scene = next(
                    (item for item in asset.scenes if item.id == clip.scene_id), None
                )
                if scene and clip.source_start < scene.end:
                    available = min(available, scene.end - clip.source_start)
            source_duration = min(max(0, available), clip.duration * clip.speed)
            if source_duration > 0.001:
                source = asset_path(p.id, asset)
                run_ff(
                    [
                        "-ss",
                        clip.source_start,
                        "-t",
                        source_duration,
                        "-i",
                        source,
                        "-map",
                        "0:a:0",
                        "-af",
                        f"{_atempo(clip.speed)},aresample=48000,apad,atrim=duration={clip.duration},asetpts=PTS-STARTPTS",
                        "-ac",
                        "2",
                        "-ar",
                        "48000",
                        "-c:a",
                        "pcm_s16le",
                        "-t",
                        clip.duration,
                        output,
                    ],
                    job=job,
                )
            else:
                run_ff(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=48000:cl=stereo",
                        "-t",
                        clip.duration,
                        "-c:a",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        output,
                    ],
                    job=job,
                )
        else:
            run_ff(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-t",
                    clip.duration,
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    output,
                ],
                job=job,
            )
        paths.append(output)
    args = []
    filters = []
    for index, path in enumerate(paths):
        args += ["-i", path]
        filters.append(f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]")
    last = "a0"
    elapsed = p.clips[0].duration
    for index, clip in enumerate(p.clips[1:], 1):
        overlap = overlap_before(p.clips, index)
        label = f"joined_a{index}"
        if overlap:
            filters.append(
                f"[{last}][a{index}]acrossfade=d={overlap}:c1=tri:c2=tri[{label}]"
            )
            elapsed += clip.duration - overlap
        else:
            filters.append(f"[{last}][a{index}]concat=n=2:v=0:a=1[{label}]")
            elapsed += clip.duration
        last = label
    output = d / "source_track.wav"
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{last}]",
        "-t",
        duration,
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        output,
    ]
    run_ff(args, cwd=d, job=job)
    return output


def _validate_audio_clock(path, duration):
    with av.open(str(path)) as container:
        pts = [packet.pts for packet in container.demux(audio=0) if packet.size]
    expected = math.ceil(round(duration * 48000) / 1024) + 1
    if (
        len(pts) != expected
        or any(value is None for value in pts)
        or any(right - left != 1024 for left, right in zip(pts, pts[1:]))
    ):
        raise ValueError("Timestamp âm thanh xuất không hợp lệ. Hãy thử xuất lại.")


def _validate_video_clock(path, duration):
    with av.open(str(path)) as container:
        count = 0
        for frame in container.decode(video=0):
            if (
                frame.pts is None
                or abs(float(frame.pts * frame.time_base) - count / 30) > 0.0001
            ):
                raise ValueError("Timestamp hình ảnh xuất không đều. Hãy thử xuất lại.")
            count += 1
    if count != round(duration * 30):
        raise ValueError("Số khung hình xuất không khớp timeline. Hãy thử xuất lại.")


def render(p, job, preview=False):
    original_project = p.model_copy(deep=True)
    report = preflight(p, project_dir(p.id))
    errors = [issue for issue in report["issues"] if issue["severity"] == "error"]
    if errors:
        first = errors[0]
        raise ValueError(
            f"Preflight {first['code']}: {first['message']} {first['fix']}"
        )
    validate_timeline(p)
    p = _frame_project(p)
    sound_effects.refresh(p)
    duration = round(timeline_duration(p.clips) * 30) / 30
    d = project_dir(p.id) / "exports" / job.id
    d.mkdir(parents=True, exist_ok=True)
    width, height = dimensions(p.aspect, "720" if preview else p.resolution)
    fps = 30
    vp = p.template.viewport
    vw = max(2, int(width * vp.w) // 2 * 2)
    vh = max(2, int(height * vp.h) // 2 * 2)
    vx = min(width - vw, round(width * vp.x))
    vy = min(height - vh, round(height * vp.y))
    encoder = list(video_encoder())
    initial_encoder = encoder[0]
    cache_hits = 0
    proxy_count = 0
    for index, clip in enumerate(p.clips):
        job.update(5 + 65 * index / len(p.clips), f"Dựng cảnh {index+1}/{len(p.clips)}")
        asset = get_asset(p, clip.asset_id)
        source = asset_path(p.id, asset)
        if preview and asset.media == "video":
            proxy = proxy_for_preview(p.id, asset, job)
            if str(proxy) != str(source):
                proxy_count += 1
                source = proxy
        layers = _layer_list(p, clip, index)
        _, hit = _render_clip(
            p,
            clip,
            asset,
            index,
            layers,
            source,
            d,
            width,
            height,
            vw,
            vh,
            vx,
            vy,
            preview,
            encoder,
            job,
        )
        cache_hits += int(hit)
    job.update(74, "Ghép cảnh trên trục thời gian chung")
    _join_video(p, d, job, duration, encoder, preview)
    source_track = _source_audio_track(p, d, job, duration)
    job.update(80, "Tạo phụ đề và trộn âm thanh")
    subtitles(p, width, height, d)
    # Encode audio independently: demuxing concatenated H.264 and WAV in the same
    # filter graph can duplicate AAC packets even with a sample-based audio clock.
    args = []
    filters = []
    index = 0
    voice_active = bool(p.voice_id and p.voice_volume > 0)
    music_active = bool(p.music_id and p.music_volume > 0)
    effects_active = bool(p.sound_effects and p.sound_effect_volume > 0)
    source_active = source_track is not None
    if source_active:
        source_index = index
        index += 1
        args += ["-i", "source_track.wav"]
        filters.append(
            f"[{source_index}:a]aresample=48000,atrim=duration={duration},asetpts=PTS-STARTPTS[source_raw]"
        )
    voice_index = None
    if voice_active:
        voice_index = index
        index += 1
        voice = asset_path(p.id, get_asset(p, p.voice_id))
        cached = normalized_audio(voice, store.ROOT / "cache", job)
        args += ["-i", cached]
        filters.append(
            f"[{voice_index}:a]aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[voice_raw]"
        )
    music_index = None
    if music_active:
        music_index = index
        index += 1
        music = asset_path(p.id, get_asset(p, p.music_id))
        cached = normalized_audio(music, store.ROOT / "cache", job)
        args += ["-stream_loop", "-1", "-i", cached]
        filters.append(
            f"[{music_index}:a]aresample=48000,atrim=duration={duration},asetpts=PTS-STARTPTS[music_raw]"
        )
    effect_index = None
    if effects_active:
        effect_index = index
        index += 1
        args += ["-i", sound_effects.track_path(p)]
        filters.append(f"[{effect_index}:a]aresample=48000,asetpts=N/SR/TB[effects]")
    limiter = "alimiter=limit=0.95:attack=5:release=50:level=disabled:latency=1"
    music_fade = f",afade=t=out:st={max(0,duration-1)}:d={min(1,duration)}"
    mix_inputs = []
    if source_active:
        filters.append(f"[source_raw]volume={p.source_volume}[source]")
        mix_inputs.append("[source]")
    if voice_index is not None and music_index is not None and p.ducking:
        filters += [
            "[voice_raw]asplit=2[voice_gain_src][voice_side]",
            "[music_raw][voice_side]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[music_ducked]",
            f"[voice_gain_src]volume={p.voice_volume}[voice]",
            f"[music_ducked]volume={p.music_volume}{music_fade}[music]",
        ]
    else:
        if voice_index is not None:
            filters.append(f"[voice_raw]volume={p.voice_volume}[voice]")
        if music_index is not None:
            filters.append(f"[music_raw]volume={p.music_volume}{music_fade}[music]")
    if voice_index is not None:
        mix_inputs.append("[voice]")
    if music_index is not None:
        mix_inputs.append("[music]")
    if effect_index is not None:
        mix_inputs.append("[effects]")
    if mix_inputs:
        # Rebuild the AAC clock from the actual sample count after resampling/mixing.
        filters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:duration=longest:normalize=0,{limiter},"
            f"atrim=end_sample={round(duration*48000)},asettb=1/48000,asetpts=N[audio]"
        )
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        filters.append(
            f"[{index}:a]atrim=end_sample={round(duration*48000)},asettb=1/48000,asetpts=N[audio]"
        )
    has_captions = bool(
        p.template.caption.enabled and (p.cues or any(clip.caption for clip in p.clips))
    )
    output = "preview.mp4" if preview else "video.mp4"
    args += [
        "-filter_complex_threads",
        "1",
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[audio]",
        "-t",
        duration,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "audio.m4a",
    ]
    run_ff(args, cwd=d, job=job)
    job.update(88, "Xuất hình và phụ đề")
    video_args = [
        "-i",
        "joined.mp4",
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        "subtitles=captions.ass" if has_captions else "null",
        "-frames:v",
        round(duration * 30),
        "-r",
        "30",
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        "15360",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    _run_video_encode(video_args, "final_video.mp4", preview, encoder, d, job)
    job.update(96, "Ghép hình và âm thanh")
    run_ff(
        [
            "-i",
            "final_video.mp4",
            "-i",
            "audio.m4a",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-t",
            duration,
            "-movflags",
            "+faststart",
            output,
        ],
        cwd=d,
        job=job,
    )
    _validate_audio_clock(d / output, duration)
    _validate_video_clock(d / output, duration)
    info = probe(d / output)
    if abs(info["duration"] - duration) > 0.3:
        raise ValueError("Thời lượng xuất không khớp timeline")
    snapshot = original_project.model_dump()
    (d / "project.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for path in d.iterdir():
        if path.name not in [output, "captions.srt", "captions.ass", "project.json"]:
            path.unlink()
    return {
        "id": job.id,
        "filename": output,
        "duration": round(info["duration"], 2),
        "width": width,
        "height": height,
        "created_at": now(),
        "preview": preview,
        "size": (d / output).stat().st_size,
        "encoder": encoder[0],
        "hardware_encoder": encoder[1],
        "clip_cache_hits": cache_hits,
        "clip_cache_misses": len(p.clips) - cache_hits,
        "proxies_used": proxy_count,
        "encoder_fallback": initial_encoder != encoder[0],
        "render_version": RENDER_VERSION,
        "fps": 30,
        "clock_verified": True,
        "sound_effect_count": len(p.sound_effects),
    }
