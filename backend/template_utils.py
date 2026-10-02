"""Utilities for fitting detected reference beats into the template schema."""

from __future__ import annotations

import math

MAX_TEMPLATE_SLOTS = 100


def compressed_slot_durations(asset):
    """Return <=100 ordered beats covering the complete reference duration.

    When a reference has too many detected scenes, adjacent scenes share one
    beat.  The source scene list is never modified; gaps and trailing silence
    are included in the preceding beat so the resulting durations still cover
    the complete source timeline.
    """
    scenes = asset.scenes
    if not scenes:
        duration = round(float(asset.duration or 0), 3)
        return [duration] if duration > 0 else []

    total = max(float(asset.duration or 0), max(float(scene.end) for scene in scenes))
    count = min(len(scenes), MAX_TEMPLATE_SLOTS)
    boundaries = [0.0]
    for slot in range(1, count):
        scene_index = (slot * len(scenes)) // count
        boundaries.append(float(scenes[scene_index].start))
    boundaries.append(total)

    durations = [
        max(0.0, boundaries[index + 1] - boundaries[index]) for index in range(count)
    ]
    # Floating-point rounding is applied only to each slot; correct the last
    # slot so the sum remains the exact full source duration to millisecond.
    rounded = [round(value, 3) for value in durations]
    rounded[-1] = round(total - sum(rounded[:-1]), 3)
    if any(not math.isfinite(value) or value < 0 for value in rounded):
        raise ValueError(
            "Không thể gộp nhịp template theo thứ tự thời gian. Hãy phân tích lại video mẫu."
        )
    return rounded
