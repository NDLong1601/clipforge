"""Shared, non-mutating checks run by the API and immediately before render."""

from __future__ import annotations

from pathlib import Path

from . import media
from .models import Project
from .font_manager import resolve
from .timeline_math import overlap_before, timeline_duration


def preflight(project: Project, project_root: Path | None = None) -> dict:
    issues: list[dict] = []

    def add(code, severity, entity_type, entity_id, message, fix):
        issues.append(
            {
                "code": code,
                "severity": severity,
                "entity": {"type": entity_type, "id": entity_id},
                "message": message,
                "fix": fix,
            }
        )

    assets = {asset.id: asset for asset in project.assets}
    duration = timeline_duration(project.clips)
    overlaps = [
        overlap_before(project.clips, index) for index in range(len(project.clips))
    ]
    if duration > 180.1:
        add(
            "timeline.too_long",
            "error",
            "project",
            project.id,
            f"Tổng timeline {duration:.2f}s vượt giới hạn 180s.",
            "Rút ngắn hoặc xóa bớt cảnh.",
        )
    if not project.clips:
        add(
            "timeline.empty",
            "error",
            "project",
            project.id,
            "Timeline chưa có cảnh.",
            "Thêm cảnh hoặc tự động dựng timeline.",
        )
    for event in project.sound_effects:
        if event.origin == "manual" and event.time >= duration:
            add(
                "sound_effect.outside_timeline",
                "warning",
                "project",
                project.id,
                f"Hiệu ứng {event.effect} tại {event.time:.2f}s nằm ngoài timeline và sẽ không được phát.",
                "Chỉnh mốc hiệu ứng trong tab Âm thanh.",
            )
    if project.cues_stale:
        add(
            "cue.script_changed",
            "warning",
            "project",
            project.id,
            "Kịch bản đã đổi; nội dung và mốc phụ đề cũ đang được giữ lại.",
            "Kiểm tra cue hoặc chủ động tạo lại mốc phụ đề.",
        )

    referenced = set()
    clip_asset_ids = {clip.asset_id for clip in project.clips}
    referenced.update(asset.id for asset in project.assets if not asset.deleted)
    for clip in project.clips:
        referenced.add(clip.asset_id)
        asset = assets.get(clip.asset_id)
        if asset is None:
            add(
                "clip.asset_missing",
                "error",
                "clip",
                clip.id,
                f"Cảnh {clip.id} không tìm thấy tư liệu {clip.asset_id}.",
                "Chọn lại tư liệu nguồn cho cảnh.",
            )
            continue
        if (
            asset.deleted
            or asset.role != "source"
            or asset.media not in {"video", "image"}
        ):
            add(
                "clip.media_type",
                "error",
                "clip",
                clip.id,
                f"Tư liệu “{asset.name}” không phải hình/video nguồn đang hoạt động.",
                "Thay bằng tư liệu hình hoặc video nguồn đang hoạt động.",
            )
        if project.avoid_faces:
            from .face_filter import cached_clip_status

            status = cached_clip_status(project, clip)
            if status != "clear":
                add(
                    (
                        "clip.face_present"
                        if status == "present"
                        else "clip.face_unchecked"
                    ),
                    "error",
                    "clip",
                    clip.id,
                    f"Cảnh “{asset.name}” có mặt hoặc chưa được kiểm tra theo mốc cắt hiện tại.",
                    "Bấm Lọc lại cảnh có mặt; mở khóa cảnh cần thay trước khi lọc.",
                )
        if asset.media == "video":
            source_end = clip.source_start + clip.duration * clip.speed
            limit = asset.duration
            if clip.scene_id:
                scene = next(
                    (item for item in asset.scenes if item.id == clip.scene_id), None
                )
                if scene is None:
                    add(
                        "clip.scene_missing",
                        "error",
                        "clip",
                        clip.id,
                        f"Cảnh nguồn “{clip.scene_id}” không còn thuộc “{asset.name}”.",
                        "Chọn lại cảnh nguồn hoặc chỉnh mốc cắt.",
                    )
                else:
                    limit = min(limit, scene.end)
            frame_tolerance = max(0.001, clip.speed / max(asset.fps or 30, 1))
            if (
                clip.source_start < 0
                or clip.source_start >= asset.duration - 0.01
                or (
                    clip.scene_id
                    and scene is not None
                    and (
                        clip.source_start < scene.start
                        or clip.source_start >= scene.end
                    )
                )
            ):
                add(
                    "clip.source_out_of_range",
                    "error",
                    "clip",
                    clip.id,
                    f"Mốc bắt đầu nguồn của “{asset.name}” vượt thời lượng cảnh/tư liệu.",
                    "Chỉnh mốc cắt, thời lượng hoặc tốc độ cảnh.",
                )
            elif source_end > limit + frame_tolerance:
                held_seconds = (source_end - limit) / max(clip.speed, 0.01)
                add(
                    "clip.source_frame_hold",
                    "warning",
                    "clip",
                    clip.id,
                    f"Cảnh “{asset.name}” cần giữ khung hình cuối khoảng {held_seconds:.2f}s "
                    f"(đã tính tốc độ ×{clip.speed:g} và giới hạn cảnh/nguồn).",
                    "Thay cảnh hoặc rút thời lượng nếu không muốn giữ khung cuối.",
                )
        for layer in clip.layers:
            if layer.kind == "image":
                if not layer.asset_id:
                    add(
                        "layer.image_source_missing",
                        "error",
                        "layer",
                        layer.id,
                        "Lớp ảnh chưa chọn tư liệu nguồn.",
                        "Chọn logo/ảnh cho lớp này hoặc xóa lớp.",
                    )
                else:
                    referenced.add(layer.asset_id)

    for index, clip in enumerate(project.clips):
        overlap = overlaps[index]
        next_overlap = overlaps[index + 1] if index + 1 < len(overlaps) else 0
        if overlap and overlap >= clip.duration:
            add(
                "transition.duration_invalid",
                "error",
                "clip",
                clip.id,
                f"Crossfade {overlap:.2f}s dài bằng hoặc hơn cảnh {index+1}.",
                "Giảm thời lượng crossfade hoặc kéo dài cảnh.",
            )
        if overlap + next_overlap > clip.duration - 0.001:
            add(
                "transition.overlap_collision",
                "error",
                "clip",
                clip.id,
                f"Hai crossfade chồng quá nhiều lên cảnh {index+1}.",
                "Giảm thời lượng chuyển cảnh để mỗi cảnh còn phần hình riêng.",
            )
        if index and overlap:
            for affected in (project.clips[index - 1], clip):
                asset = assets.get(affected.asset_id)
                if not asset or asset.media != "video":
                    continue
                limit = asset.duration
                scene = (
                    next(
                        (item for item in asset.scenes if item.id == affected.scene_id),
                        None,
                    )
                    if affected.scene_id
                    else None
                )
                if scene:
                    limit = min(limit, scene.end)
                available = max(
                    0, (limit - affected.source_start) / max(affected.speed, 0.01)
                )
                if (
                    available + max(0.001, 1 / max(asset.fps or 30, 1))
                    < affected.duration
                ):
                    add(
                        "transition.handle_missing",
                        "error",
                        "clip",
                        affected.id,
                        f"Không đủ hình nguồn cho crossfade cạnh cảnh {index+1}; phần cuối sẽ giữ ảnh đứng.",
                        "Cắt ngắn cảnh, chọn cảnh nguồn dài hơn hoặc giảm tốc độ/chuyển cảnh.",
                    )

    for label, asset_id, expected_role in (
        ("voice", project.voice_id, "voice"),
        ("music", project.music_id, "music"),
    ):
        if not asset_id:
            continue
        referenced.add(asset_id)
        asset = assets.get(asset_id)
        if asset is None:
            add(
                f"{label}.asset_missing",
                "error",
                label,
                asset_id,
                f"Không tìm thấy tư liệu {label} {asset_id}.",
                f"Chọn lại hoặc gỡ {label} này.",
            )
        elif asset.deleted or asset.role != expected_role or not asset.has_audio:
            add(
                f"{label}.media_type",
                "error",
                label,
                asset_id,
                f"Tư liệu “{asset.name}” không phải {label} có âm thanh đang hoạt động.",
                f"Chọn một file {label} hợp lệ.",
            )
        elif label == "voice" and asset.duration > duration + 0.15:
            add(
                "voice.longer_than_timeline",
                "error",
                label,
                asset_id,
                f"Voice dài {asset.duration:.2f}s nhưng timeline chỉ dài {duration:.2f}s.",
                "Kéo dài timeline hoặc chọn voice ngắn hơn để không mất lời.",
            )

    template_layers = list(project.template.layers)
    for slot in project.template.slot_layers:
        template_layers.extend(slot)
    for layer in template_layers:
        if layer.kind != "image":
            continue
        if not layer.asset_id:
            add(
                "layer.image_source_missing",
                "error",
                "layer",
                layer.id,
                "Lớp ảnh trong template chưa chọn tư liệu nguồn.",
                "Chọn logo/ảnh cho lớp này hoặc xóa lớp.",
            )
            continue
        referenced.add(layer.asset_id)
        asset = assets.get(layer.asset_id)
        if asset is None or asset.deleted:
            add(
                "layer.image_source_missing",
                "error",
                "layer",
                layer.id,
                f"Lớp ảnh trỏ tới tư liệu không tồn tại: {layer.asset_id}.",
                "Chọn lại logo/ảnh hoặc xóa lớp.",
            )
        elif asset.media != "image":
            add(
                "layer.media_type",
                "error",
                "layer",
                layer.id,
                f"Tư liệu “{asset.name}” của lớp ảnh không phải ảnh.",
                "Chọn file ảnh PNG, JPG hoặc WEBP.",
            )

    font_names = {project.template.caption.font_family}
    font_names.update(
        layer.font_family for layer in template_layers if layer.kind == "text"
    )
    for clip in project.clips:
        font_names.update(
            layer.font_family for layer in clip.layers if layer.kind == "text"
        )
    for family in sorted(font_names):
        _, fallback, found = resolve(family)
        if not found:
            add(
                "font.missing",
                "warning",
                "project",
                project.id,
                f"Không tìm thấy font “{family}”; renderer sẽ dùng {fallback}.",
                "Chọn font khả dụng để preview và video xuất khớp hơn.",
            )

    for index, cue in enumerate(project.cues):
        cue_id = str(index + 1)
        if cue.start < 0 or cue.end <= cue.start or not cue.text.strip():
            add(
                "cue.invalid",
                "error",
                "cue",
                cue_id,
                f"Cue phụ đề {index + 1} có nội dung hoặc mốc thời gian không hợp lệ.",
                "Sửa nội dung và bảo đảm mốc kết thúc lớn hơn mốc bắt đầu.",
            )
        elif cue.end > duration + 0.01:
            add(
                "cue.outside_timeline",
                "error",
                "cue",
                cue_id,
                f"Cue phụ đề {index + 1} kết thúc ở {cue.end:.2f}s, sau timeline {duration:.2f}s.",
                "Kéo dài timeline hoặc chỉnh lại cue; nội dung cue được giữ nguyên.",
            )

    if project_root is not None:
        assets_root = (project_root / "assets").resolve()
        for asset_id in sorted(referenced):
            asset = assets.get(asset_id)
            if asset is None or asset.deleted:
                continue
            path = (assets_root / asset.filename).resolve()
            if path.parent != assets_root:
                add(
                    "asset.path_invalid",
                    "error",
                    "asset",
                    asset.id,
                    f"Đường dẫn tư liệu “{asset.name}” không hợp lệ.",
                    "Nhập lại tư liệu vào dự án.",
                )
            elif not path.is_file():
                add(
                    "asset.file_missing",
                    "error",
                    "asset",
                    asset.id,
                    f"Thiếu file tư liệu “{asset.name}” ({asset.filename}).",
                    "Nhập lại file hoặc thay tư liệu khác.",
                )
            else:
                try:
                    actual = media.probe(path)
                except (OSError, ValueError):
                    add(
                        "asset.media_unreadable",
                        "error",
                        "asset",
                        asset.id,
                        f"Không đọc được file tư liệu “{asset.name}”.",
                        "Nhập lại file media hợp lệ.",
                    )
                    continue
                expected = asset.media
                if actual["media"] != expected:
                    add(
                        "asset.media_type_mismatch",
                        "error",
                        "asset",
                        asset.id,
                        f'File “{asset.name}” thực tế là {actual["media"]}, dự án ghi nhận là {expected}.',
                        "Nhập lại file đúng loại hoặc cập nhật tư liệu.",
                    )
                elif asset.role in {"voice", "music"} and not actual["has_audio"]:
                    add(
                        "asset.audio_missing",
                        "error",
                        "asset",
                        asset.id,
                        f"File “{asset.name}” không có luồng âm thanh đọc được.",
                        "Chọn lại một file có âm thanh.",
                    )
                elif (
                    asset.id in clip_asset_ids
                    and asset.role == "source"
                    and asset.media == "video"
                    and project.source_volume > 0
                    and not actual["has_audio"]
                ):
                    add(
                        "source.audio_missing",
                        "warning",
                        "asset",
                        asset.id,
                        f"Video nguồn “{asset.name}” không có âm thanh; không thể trộn âm thanh gốc từ file này.",
                        "Chọn video có âm thanh hoặc giảm âm lượng nguồn về 0.",
                    )
                elif asset.role == "reference" and actual["media"] != "video":
                    add(
                        "asset.reference_not_video",
                        "error",
                        "asset",
                        asset.id,
                        f"Video mẫu “{asset.name}” không phải video.",
                        "Chọn lại video mẫu.",
                    )

    return {
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
    }
