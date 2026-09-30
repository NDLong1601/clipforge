"""Shared, non-mutating checks run by the API and immediately before render."""
from __future__ import annotations

from pathlib import Path

from . import media
from .models import Project


def preflight(project: Project, project_root: Path | None = None) -> dict:
    issues: list[dict] = []

    def add(code, severity, entity_type, entity_id, message, fix):
        issues.append({
            'code': code,
            'severity': severity,
            'entity': {'type': entity_type, 'id': entity_id},
            'message': message,
            'fix': fix,
        })

    assets = {asset.id: asset for asset in project.assets}
    duration = sum(clip.duration for clip in project.clips)
    if duration > 180.1:
        add('timeline.too_long', 'error', 'project', project.id,
            f'Tổng timeline {duration:.2f}s vượt giới hạn 180s.',
            'Rút ngắn hoặc xóa bớt cảnh.')
    if not project.clips:
        add('timeline.empty', 'error', 'project', project.id,
            'Timeline chưa có cảnh.', 'Thêm cảnh hoặc tự động dựng timeline.')
    if project.cues_stale:
        add('cue.script_changed', 'warning', 'project', project.id,
            'Kịch bản đã đổi; nội dung và mốc phụ đề cũ đang được giữ lại.',
            'Kiểm tra cue hoặc chủ động tạo lại mốc phụ đề.')

    referenced = set()
    referenced.update(asset.id for asset in project.assets if not asset.deleted)
    for clip in project.clips:
        referenced.add(clip.asset_id)
        asset = assets.get(clip.asset_id)
        if asset is None:
            add('clip.asset_missing', 'error', 'clip', clip.id,
                f'Cảnh {clip.id} không tìm thấy tư liệu {clip.asset_id}.',
                'Chọn lại tư liệu nguồn cho cảnh.')
            continue
        if asset.deleted or asset.role != 'source' or asset.media not in {'video', 'image'}:
            add('clip.media_type', 'error', 'clip', clip.id,
                f'Tư liệu “{asset.name}” không phải hình/video nguồn đang hoạt động.',
                'Thay bằng tư liệu hình hoặc video nguồn đang hoạt động.')
        if asset.media == 'video':
            source_end = clip.source_start + clip.duration * clip.speed
            limit = asset.duration
            if clip.scene_id:
                scene = next((item for item in asset.scenes if item.id == clip.scene_id), None)
                if scene is None:
                    add('clip.scene_missing', 'error', 'clip', clip.id,
                        f'Cảnh nguồn “{clip.scene_id}” không còn thuộc “{asset.name}”.',
                        'Chọn lại cảnh nguồn hoặc chỉnh mốc cắt.')
                else:
                    limit = min(limit, scene.end)
            if clip.source_start < 0 or clip.source_start >= asset.duration - .01 or (
                clip.scene_id and scene is not None and
                (clip.source_start < scene.start or clip.source_start >= scene.end)
            ):
                add('clip.source_out_of_range', 'error', 'clip', clip.id,
                    f'Mốc bắt đầu nguồn của “{asset.name}” vượt thời lượng cảnh/tư liệu.',
                    'Chỉnh mốc cắt, thời lượng hoặc tốc độ cảnh.')
            elif source_end > limit + .05:
                add('clip.source_frame_hold', 'warning', 'clip', clip.id,
                    f'Cảnh “{asset.name}” dài hơn đoạn nguồn; renderer sẽ giữ khung hình cuối.',
                    'Thay cảnh hoặc rút thời lượng nếu không muốn giữ khung cuối.')
        for layer in clip.layers:
            if layer.kind == 'image':
                if not layer.asset_id:
                    add('layer.image_source_missing', 'error', 'layer', layer.id,
                        'Lớp ảnh chưa chọn tư liệu nguồn.', 'Chọn logo/ảnh cho lớp này hoặc xóa lớp.')
                else:
                    referenced.add(layer.asset_id)

    for label, asset_id, expected_role in (
        ('voice', project.voice_id, 'voice'), ('music', project.music_id, 'music')
    ):
        if not asset_id:
            continue
        referenced.add(asset_id)
        asset = assets.get(asset_id)
        if asset is None:
            add(f'{label}.asset_missing', 'error', label, asset_id,
                f'Không tìm thấy tư liệu {label} {asset_id}.', f'Chọn lại hoặc gỡ {label} này.')
        elif asset.deleted or asset.role != expected_role or not asset.has_audio:
            add(f'{label}.media_type', 'error', label, asset_id,
                f'Tư liệu “{asset.name}” không phải {label} có âm thanh đang hoạt động.',
                f'Chọn một file {label} hợp lệ.')
        elif label == 'voice' and asset.duration > duration + .15:
            add('voice.longer_than_timeline', 'error', label, asset_id,
                f'Voice dài {asset.duration:.2f}s nhưng timeline chỉ dài {duration:.2f}s.',
                'Kéo dài timeline hoặc chọn voice ngắn hơn để không mất lời.')

    template_layers = list(project.template.layers)
    for slot in project.template.slot_layers:
        template_layers.extend(slot)
    for layer in template_layers:
        if layer.kind != 'image':
            continue
        if not layer.asset_id:
            add('layer.image_source_missing', 'error', 'layer', layer.id,
                'Lớp ảnh trong template chưa chọn tư liệu nguồn.',
                'Chọn logo/ảnh cho lớp này hoặc xóa lớp.')
            continue
        referenced.add(layer.asset_id)
        asset = assets.get(layer.asset_id)
        if asset is None or asset.deleted:
            add('layer.image_source_missing', 'error', 'layer', layer.id,
                f'Lớp ảnh trỏ tới tư liệu không tồn tại: {layer.asset_id}.',
                'Chọn lại logo/ảnh hoặc xóa lớp.')
        elif asset.media != 'image':
            add('layer.media_type', 'error', 'layer', layer.id,
                f'Tư liệu “{asset.name}” của lớp ảnh không phải ảnh.',
                'Chọn file ảnh PNG, JPG hoặc WEBP.')

    for index, cue in enumerate(project.cues):
        cue_id = str(index + 1)
        if cue.start < 0 or cue.end <= cue.start or not cue.text.strip():
            add('cue.invalid', 'error', 'cue', cue_id,
                f'Cue phụ đề {index + 1} có nội dung hoặc mốc thời gian không hợp lệ.',
                'Sửa nội dung và bảo đảm mốc kết thúc lớn hơn mốc bắt đầu.')
        elif cue.end > duration + .01:
            add('cue.outside_timeline', 'error', 'cue', cue_id,
                f'Cue phụ đề {index + 1} kết thúc ở {cue.end:.2f}s, sau timeline {duration:.2f}s.',
                'Kéo dài timeline hoặc chỉnh lại cue; nội dung cue được giữ nguyên.')

    if project_root is not None:
        assets_root = (project_root / 'assets').resolve()
        for asset_id in sorted(referenced):
            asset = assets.get(asset_id)
            if asset is None or asset.deleted:
                continue
            path = (assets_root / asset.filename).resolve()
            if path.parent != assets_root:
                add('asset.path_invalid', 'error', 'asset', asset.id,
                    f'Đường dẫn tư liệu “{asset.name}” không hợp lệ.',
                    'Nhập lại tư liệu vào dự án.')
            elif not path.is_file():
                add('asset.file_missing', 'error', 'asset', asset.id,
                    f'Thiếu file tư liệu “{asset.name}” ({asset.filename}).',
                    'Nhập lại file hoặc thay tư liệu khác.')
            else:
                try:
                    actual = media.probe(path)
                except (OSError, ValueError):
                    add('asset.media_unreadable', 'error', 'asset', asset.id,
                        f'Không đọc được file tư liệu “{asset.name}”.',
                        'Nhập lại file media hợp lệ.')
                    continue
                expected = asset.media
                if actual['media'] != expected:
                    add('asset.media_type_mismatch', 'error', 'asset', asset.id,
                        f'File “{asset.name}” thực tế là {actual["media"]}, dự án ghi nhận là {expected}.',
                        'Nhập lại file đúng loại hoặc cập nhật tư liệu.')
                elif asset.role in {'voice', 'music'} and not actual['has_audio']:
                    add('asset.audio_missing', 'error', 'asset', asset.id,
                        f'File “{asset.name}” không có luồng âm thanh đọc được.',
                        'Chọn lại một file có âm thanh.')
                elif asset.role == 'reference' and actual['media'] != 'video':
                    add('asset.reference_not_video', 'error', 'asset', asset.id,
                        f'Video mẫu “{asset.name}” không phải video.',
                        'Chọn lại video mẫu.')

    return {'ok': not any(issue['severity'] == 'error' for issue in issues), 'issues': issues}
