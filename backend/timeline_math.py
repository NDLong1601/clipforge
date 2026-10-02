"""Shared project-time calculations for clip transitions."""

from __future__ import annotations


def overlap_before(clips, index: int) -> float:
    if index <= 0 or index >= len(clips):
        return 0.0
    clip = clips[index]
    if clip.transition != "crossfade":
        return 0.0
    return float(clip.transition_duration)


def clip_starts(clips) -> list[float]:
    starts: list[float] = []
    position = 0.0
    for index, clip in enumerate(clips):
        if index:
            position -= overlap_before(clips, index)
        starts.append(max(0.0, position))
        position += float(clip.duration)
    return starts


def timeline_duration(clips) -> float:
    if not clips:
        return 0.0
    starts = clip_starts(clips)
    return starts[-1] + float(clips[-1].duration)


def transition_overlaps(clips) -> list[float]:
    return [overlap_before(clips, index) for index in range(len(clips))]


def caption_windows(clips) -> list[tuple[float, float]]:
    """The incoming clip owns captions from the start of its transition."""
    starts = clip_starts(clips)
    return [
        (
            start,
            (
                min(start + float(clip.duration), starts[index + 1])
                if index + 1 < len(clips)
                else start + float(clip.duration)
            ),
        )
        for index, (start, clip) in enumerate(zip(starts, clips))
    ]
