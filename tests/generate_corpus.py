"""Generate the repeatable M0 media corpus outside the repository."""

from __future__ import annotations

import argparse
import av
import hashlib
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend import media
from tests.fixtures.factory import make_transparent_logo


def _encode_video(path: Path, size: str, duration: int, fps: int = 30) -> None:
    media.run_ff(
        [
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
            "-t", str(duration), "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "30", "-pix_fmt", "yuv420p", path,
        ]
    )


def _rotation_degrees(path: Path) -> int | None:
    with av.open(str(path)) as container:
        stream = next(stream for stream in container.streams if stream.type == "video")
        frame = next(container.decode(stream))
        return int(frame.rotation)


def _distinct_frame_intervals(path: Path) -> int:
    with av.open(str(path)) as container:
        stream = next(stream for stream in container.streams if stream.type == "video")
        frames = list(container.decode(stream))
    intervals = {
        round(float(right.pts * right.time_base - left.pts * left.time_base), 5)
        for left, right in zip(frames, frames[1:])
    }
    return len(intervals)


def generate(
    output: Path,
    long_seconds: int = 60,
    real_video: Path | None = None,
    vietnamese_voice: Path | None = None,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    portrait = output / "portrait_30fps.mp4"
    landscape = output / "landscape_30fps.mp4"
    vfr = output / "variable_framerate.mp4"
    rotated = output / "rotated_metadata.mov"
    long_video = output / f"long_{long_seconds}s.mp4"
    _encode_video(portrait, "240x426", 4)
    _encode_video(landscape, "426x240", 4)
    media.run_ff(
        [
            "-f", "lavfi", "-i", "testsrc2=size=426x240:rate=30", "-t", "5",
            "-vf", "select='not(mod(n,3))+not(mod(n+1,5))',setpts=PTS",
            "-fps_mode", "vfr", "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "30", "-pix_fmt", "yuv420p", vfr,
        ]
    )
    media.run_ff(["-display_rotation", "90", "-i", landscape, "-c", "copy", rotated])
    _encode_video(long_video, "240x426", long_seconds, fps=15)
    for name, frequency in (("voice_tone.wav", 440), ("music_tone.wav", 220)):
        media.run_ff(
            [
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
                "-t", "5", "-c:a", "pcm_s16le", output / name,
            ]
        )
    make_transparent_logo(output / "transparent_logo.png")

    files = [portrait, landscape, vfr, rotated, long_video]
    files.extend((output / "voice_tone.wav", output / "music_tone.wav", output / "transparent_logo.png"))
    optional_inputs = {}
    for source, name, expected_media in (
        (real_video, "real_acceptance_video", "video"),
        (vietnamese_voice, "vietnamese_voice_input", "audio"),
    ):
        if source is None:
            continue
        source = source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = output / f"{name}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        media_info = media.probe(destination)
        if media_info["media"] != expected_media:
            raise ValueError(f"{source.name} must be {expected_media} media")
        files.append(destination)
        optional_inputs[name] = str(source)
    rotation = _rotation_degrees(rotated)
    vfr_interval_count = _distinct_frame_intervals(vfr)
    if rotation != 90:
        raise RuntimeError(f"Rotated MOV fixture has rotation={rotation}; expected 90 degrees")
    if vfr_interval_count < 2:
        raise RuntimeError("VFR fixture has constant frame intervals")
    manifest_path = output / "manifest.json"
    manifest = {
        "purpose": "ClipForge M0 deterministic synthetic fixtures",
        "generated_media_only": True,
        "long_video_seconds": long_seconds,
        "optional_user_media_sources": optional_inputs,
        "checks": {
            "rotated_mov_display_degrees": rotation,
            "vfr_distinct_frame_intervals": vfr_interval_count,
        },
        "files": [
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "probe": media.probe(path),
            }
            for path in files
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Temporary corpus directory")
    parser.add_argument("--long-seconds", type=int, default=60)
    parser.add_argument("--real-video", type=Path, help="Copy an authorized real video into the temporary corpus")
    parser.add_argument("--vietnamese-voice", type=Path, help="Copy an authorized Vietnamese voice sample into the corpus")
    args = parser.parse_args()
    manifest = generate(args.output.resolve(), args.long_seconds, args.real_video, args.vietnamese_voice)
    print(json.dumps({"output": str(args.output.resolve()), "files": len(manifest["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
