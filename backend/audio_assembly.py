"""Build an editable timeline directly from imported narration."""

from . import media, planner, providers, store


def assemble(p, job):
    voice = store.get_asset(p, p.voice_id)
    if voice.duration > 180:
        raise ValueError(
            "Audio dựng tự động tối đa 180 giây. File đã được nhập; hãy chọn đoạn ngắn hơn."
        )
    if (
        p.cue_timing not in ("transcribed", "audio_segments")
        or not p.cues
        or p.cues_stale
    ):
        providers.transcribe(p, voice, job)
    p.target_duration = max(5, voice.duration)
    sources = [
        asset for asset in p.assets if asset.role == "source" and not asset.deleted
    ]
    if not sources:
        p.warnings = [
            "Đã phân tích lời đọc. Thêm video/ảnh nguồn để tự ghép phân cảnh theo audio."
        ]
        return p
    for index, asset in enumerate(sources):
        job.check()
        job.update(
            35 + 10 * index / len(sources), "Phân tích cảnh nguồn: " + asset.name
        )
        if not asset.scenes:
            media.analyze(p.id, asset, job)
        else:
            media.refresh_scene_thumbnails(p.id, asset, job)
    try:
        providers.ai_candidates(store.settings())
        use_ai = True
    except ValueError:
        use_ai = False
    if use_ai:
        providers.label_scenes(p, job)
    return planner.plan(p, use_ai, job)
