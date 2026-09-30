"""Build small media/project fixtures under a caller-provided temporary root."""

from __future__ import annotations

import json
import math
from pathlib import Path

import av
import numpy as np
from PIL import Image

from backend import media, store
from backend.models import Asset, Clip, Layer, Project, Scene


FIXTURE_DIR = Path(__file__).resolve().parent


def load_json_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def project_with_scene_count(count: int) -> Project:
    if count < 1:
        raise ValueError("count must be positive")
    scenes = [
        Scene(start=float(index), end=float(index + 1), thumbnail=f"scene_{index}.jpg")
        for index in range(count)
    ]
    asset = Asset(
        id="5555555555555555",
        name="Video tham chiếu fixture",
        filename="reference.mp4",
        role="reference",
        media="video",
        duration=float(count),
        width=240,
        height=320,
        fps=30,
        scenes=scenes,
    )
    project = Project(name=f"Mẫu {count} cảnh", target_duration=6, assets=[asset])
    project.mode = "template"
    # Reproduce the current API path: the model does not validate assignment.
    project.template.slot_durations = [scene.end - scene.start for scene in scenes]
    return project


def make_transparent_logo(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (96, 64), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(8, 56):
        for x in range(8, 88):
            pixels[x, y] = (181, 243, 109, 210 if 16 <= x < 80 else 0)
    image.save(path)
    return path


def make_media_project(
    *,
    name: str = "Fixture đa track",
    include_voice: bool = True,
    include_music: bool = True,
    include_logo: bool = False,
    music_volume: float = 0.15,
    voice_volume: float = 1.0,
) -> Project:
    """Create five seconds of 30 fps media inside the isolated store root."""
    project = Project(
        name=name,
        target_duration=5,
        aspect="9:16",
        resolution="720",
        music_volume=music_volume,
        voice_volume=voice_volume,
    )
    project.template.caption.enabled = False
    asset_dir = store.project_dir(project.id) / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)

    video_path = asset_dir / "source_30fps.mp4"
    media.run_ff(
        [
            "-f", "lavfi", "-i", "testsrc2=size=240x320:rate=30",
            "-t", "5", "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", video_path,
        ]
    )
    project.assets.append(
        Asset(name="Nguồn 30 fps", filename=video_path.name, role="source", **media.probe(video_path))
    )
    project.clips = [Clip(asset_id=project.assets[0].id, duration=5)]

    for role, frequency, enabled in (
        ("voice", 440, include_voice),
        ("music", 220, include_music),
    ):
        if not enabled:
            continue
        audio_path = asset_dir / f"{role}_tone.wav"
        media.run_ff(
            [
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
                "-t", "5", "-c:a", "pcm_s16le", audio_path,
            ]
        )
        asset = Asset(
            name=f"Fixture {role} tone",
            filename=audio_path.name,
            role=role,
            **media.probe(audio_path),
        )
        project.assets.append(asset)
        if role == "voice":
            project.voice_id = asset.id
        else:
            project.music_id = asset.id

    if include_logo:
        logo_path = make_transparent_logo(asset_dir / "logo.png")
        logo = Asset(
            name="Logo PNG trong suốt",
            filename=logo_path.name,
            role="overlay",
            **media.probe(logo_path),
        )
        project.assets.append(logo)
        project.template.layers.append(
            Layer(kind="image", asset_id=logo.id, x=0.72, y=0.05, w=0.2, h=0.12)
        )

    return store.save(project)


def audio_rms_db(path: Path) -> float:
    values = []
    with av.open(str(path)) as container:
        stream = next(stream for stream in container.streams if stream.type == "audio")
        for frame in container.decode(stream):
            samples = frame.to_ndarray()
            if np.issubdtype(samples.dtype, np.integer):
                samples = samples.astype(np.float64) / np.iinfo(samples.dtype).max
            else:
                samples = samples.astype(np.float64)
            values.append(samples.reshape(-1))
    if not values:
        raise ValueError(f"No audio samples in {path}")
    rms = float(np.sqrt(np.mean(np.square(np.concatenate(values)))))
    return 20 * math.log10(max(rms, 1e-12))
