"""Ten original synthesized effects and conservative speech-aware placement."""

import hashlib
import json
import os
import re
import unicodedata
import wave

import numpy as np

from . import store
from .models import SoundEffect, uid
from .timeline_math import clip_starts, timeline_duration

RATE = 48000
VERSION = "sfx-v1"
CATALOG = [
    ("whoosh", "Whoosh · Chuyển cảnh", 0.45),
    ("pop", "Pop · Bật điểm nhấn", 0.18),
    ("click", "Click · Nhấn nút", 0.12),
    ("ding", "Ding · Thông báo", 0.7),
    ("sparkle", "Sparkle · Lấp lánh", 0.85),
    ("impact", "Impact · Nhấn mạnh", 0.4),
    ("rise", "Rise · Tăng kịch tính", 0.9),
    ("swish", "Swish · Lướt nhẹ", 0.3),
    ("success", "Success · Hoàn thành", 0.8),
    ("camera", "Camera · Chụp ảnh", 0.25),
]


def catalog():
    return [
        {"id": key, "name": name, "duration": length} for key, name, length in CATALOG
    ]


def samples(effect):
    length = next((length for key, _, length in CATALOG if key == effect), None)
    if length is None:
        raise ValueError("Sound effect không hợp lệ")
    t = np.arange(round(length * RATE)) / RATE
    rng = np.random.default_rng(
        int.from_bytes(hashlib.sha256(effect.encode()).digest()[:4], "big")
    )
    noise = rng.normal(0, 1, len(t))
    soft = np.convolve(noise, np.ones(12) / 12, mode="same")
    if effect in ("whoosh", "swish", "rise"):
        envelope = np.sin(np.pi * t / length) ** 2
        y = (
            soft * envelope
            + 0.12 * np.sin(2 * np.pi * (180 * t + 700 * t * t)) * envelope
        )
    elif effect == "pop":
        y = np.sin(2 * np.pi * (700 * t - 1500 * t * t)) * np.exp(-t * 35)
    elif effect in ("click", "camera"):
        y = noise * np.exp(-t * 80)
        if effect == "camera":
            y += noise * np.exp(-np.abs(t - 0.09) * 100) * 0.65
    elif effect == "impact":
        y = (0.8 * np.sin(2 * np.pi * 80 * t) + 0.2 * soft) * np.exp(-t * 14)
    else:
        tones = {
            "ding": [880, 1760],
            "sparkle": [1318, 1760, 2093],
            "success": [523, 659, 784],
        }[effect]
        y = np.zeros(len(t))
        for index, tone in enumerate(tones):
            local = np.maximum(0, t - index * 0.12)
            y += np.where(
                t >= index * 0.12,
                np.sin(2 * np.pi * tone * local) * np.exp(-local * 7),
                0,
            )
    # Short ramps prevent clicks at the file edges; peak is safely below full scale.
    y *= np.minimum(1, t / 0.004) * np.minimum(1, (length - t) / 0.02)
    return (y / max(0.001, np.max(np.abs(y))) * 0.65).astype(np.float32)


def _write_wav(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + "." + uid() + ".tmp.wav")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def effect_path(effect):
    if effect not in {key for key, _, _ in CATALOG}:
        raise ValueError("Sound effect không hợp lệ")
    path = store.ROOT / "cache" / "sound-effects" / VERSION / (effect + ".wav")
    if not path.is_file():
        _write_wav(path, samples(effect))
    return path


def _plain(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
        if unicodedata.category(c) != "Mn"
    )


def effective_events(project):
    duration = timeline_duration(project.clips)
    manual = [event for event in project.sound_effects if event.origin == "manual"]
    if not project.auto_sound_effects or duration <= 0:
        return manual
    candidates = []
    # Combine ASR word cues before matching phrases, retaining actual word starts.
    from .planner import timed_words

    groups = []
    pending = []
    for word in timed_words(project.cues):
        if pending and (
            word.start - pending[-1].end > 0.45
            or len(pending) >= 24
            or re.search(r"[.!?。]$", pending[-1].text)
        ):
            groups.append(pending)
            pending = []
        pending.append(word)
    if pending:
        groups.append(pending)
    for group in groups:
        text = _plain(" ".join(word.text for word in group))
        effect = None
        match = re.search(r"\b(mua|dat hang|kham pha|thu ngay|dung bo lo)\b", text)
        if match:
            effect = "success"
        else:
            match = re.search(r"\b(dac biet|noi bat|quan trong|luu y|bat ngo)\b", text)
            if match:
                effect = "ding"
            else:
                match = re.search(r"\b(uu dai|giam gia|mien phi|chi \d|\d+\s*%)", text)
                if match:
                    effect = "pop"
        if effect:
            word_index = len(text[: match.start()].split())
            candidates.append(
                (group[min(word_index, len(group) - 1)].start, effect, 0.65)
            )
    # Prioritize spoken emphasis, then sparse transitions. At most one every 2s.
    candidates += [
        (start, "swish" if clip.transition == "crossfade" else "whoosh", 0.4)
        for start, clip in zip(clip_starts(project.clips)[1::3], project.clips[1::3])
    ]
    automatic = []
    maximum = min(24, max(1, int(duration / 2.5)))
    if len(manual) >= 200:
        return manual
    for time, effect, volume in candidates:
        if time >= duration - 0.12 or any(
            abs(time - event.time) < 2 for event in manual + automatic
        ):
            continue
        automatic.append(
            SoundEffect(
                id="auto-"
                + hashlib.sha256(f"{time:.6f}:{effect}".encode()).hexdigest()[:12],
                effect=effect,
                time=round(time, 6),
                volume=volume,
                origin="auto",
            )
        )
        if len(automatic) >= min(maximum, 200 - len(manual)):
            break
    return sorted(manual + automatic, key=lambda event: event.time)


def refresh(project):
    project.sound_effects = effective_events(project)
    return project


def track_path(project):
    events = effective_events(project)
    duration = timeline_duration(project.clips)
    identity = json.dumps(
        [
            VERSION,
            duration,
            project.sound_effect_volume,
            [e.model_dump() for e in events],
        ],
        sort_keys=True,
    )
    path = (
        store.ROOT
        / "cache"
        / "sound-effects"
        / (hashlib.sha256(identity.encode()).hexdigest() + ".wav")
    )
    if path.is_file():
        return path
    data = np.zeros(max(1, round(duration * RATE)), dtype=np.float32)
    for event in events:
        start = round(event.time * RATE)
        if start >= len(data):
            continue
        effect = samples(event.effect)[: len(data) - start]
        data[start : start + len(effect)] += (
            effect * event.volume * project.sound_effect_volume
        )
    if len(data) > 960:
        data[-960:] *= np.linspace(1, 0, 960)
    _write_wav(path, data)
    return path
