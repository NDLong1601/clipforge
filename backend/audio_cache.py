"""Content-addressed normalized audio cache shared by exports and previews."""
from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path

from . import media

NORMALIZATION_CONFIG = b'clipforge-audio-v1|loudnorm=I=-20:TP=-3:LRA=7|48000|stereo|pcm_s16le'
_LOCK = threading.RLock()


def normalized_audio(source: Path, cache_root: Path, job=None) -> Path:
    source = Path(source)
    digest = hashlib.sha256()
    with source.open('rb') as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    content_key = digest.hexdigest()
    config_key = hashlib.sha256(NORMALIZATION_CONFIG).hexdigest()[:16]
    destination = Path(cache_root) / 'audio' / f'{content_key}-{config_key}.wav'
    with _LOCK:
        if destination.is_file() and destination.stat().st_size > 44:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f'.{destination.stem}.{uuid.uuid4().hex}.tmp.wav')
        try:
            media.run_ff([
                '-i', source, '-vn', '-af', 'loudnorm=I=-20:TP=-3:LRA=7',
                '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', '-f', 'wav', temporary,
            ], job=job)
            if not temporary.is_file() or temporary.stat().st_size <= 44:
                raise ValueError('Không tạo được audio đã chuẩn hóa')
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination
