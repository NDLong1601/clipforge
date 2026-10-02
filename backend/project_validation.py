"""Shared project integrity checks used before persistence and media operations."""

from __future__ import annotations

from pathlib import Path

from .models import Project


def validate_project(project: Project) -> Project:
    """Validate references and structural invariants without touching the filesystem."""
    if project.schema_version != 5:
        raise ValueError(
            f"schema_version: phiên bản {project.schema_version} chưa được hỗ trợ"
        )

    assets = {}
    effect_ids = [event.id for event in project.sound_effects]
    if len(effect_ids) != len(set(effect_ids)):
        raise ValueError("sound_effects: ID hiệu ứng bị trùng")
    for index, asset in enumerate(project.assets):
        label = f"assets[{index}] ({asset.name})"
        if asset.id in assets:
            raise ValueError(f"{label}.id: ID tư liệu bị trùng")
        assets[asset.id] = asset
        if (
            not asset.filename
            or Path(asset.filename).name != asset.filename
            or asset.filename in {".", ".."}
        ):
            raise ValueError(f"{label}.filename: tên file tư liệu không hợp lệ")
        scene_ids = set()
        previous_start = -1.0
        for scene_index, scene in enumerate(asset.scenes):
            scene_label = f"{label}.scenes[{scene_index}] ({scene.id})"
            if scene.id in scene_ids:
                raise ValueError(f"{scene_label}.id: ID cảnh bị trùng")
            scene_ids.add(scene.id)
            if scene.start < previous_start:
                raise ValueError(
                    f"{scene_label}.start: cảnh không đúng thứ tự thời gian"
                )
            if asset.duration > 0 and scene.end > asset.duration + 0.25:
                raise ValueError(
                    f"{scene_label}.end: cảnh vượt thời lượng tư liệu ({asset.duration:.3f}s)"
                )
            previous_start = scene.start

    def linked_asset(asset_id: str, field: str):
        asset = assets.get(asset_id)
        if asset is None:
            raise ValueError(f"{field}: không tìm thấy tư liệu {asset_id!r}")
        if asset.deleted:
            raise ValueError(f"{field}: tư liệu đã gỡ khỏi dự án")
        return asset

    def validate_layers(layers, field):
        ids = set()
        for index, layer in enumerate(layers):
            where = f"{field}[{index}] ({layer.id})"
            if layer.id in ids:
                raise ValueError(f"{where}.id: ID lớp bị trùng")
            ids.add(layer.id)
            if layer.asset_id:
                asset = linked_asset(layer.asset_id, f"{where}.asset_id")
                if layer.kind != "image":
                    raise ValueError(
                        f"{where}.asset_id: chỉ lớp ảnh mới được tham chiếu tư liệu"
                    )
                if asset.media != "image":
                    raise ValueError(
                        f"{where}.asset_id: logo/lớp ảnh phải trỏ tới tư liệu hình ảnh"
                    )

    clip_ids = set()
    for index, clip in enumerate(project.clips):
        where = f"clips[{index}] ({clip.id})"
        if clip.id in clip_ids:
            raise ValueError(f"{where}.id: ID cảnh dựng bị trùng")
        clip_ids.add(clip.id)
        asset = linked_asset(clip.asset_id, f"{where}.asset_id")
        if asset.role != "source" or asset.media not in {"video", "image"}:
            raise ValueError(
                f"{where}.asset_id: cảnh dựng phải dùng tư liệu hình/Video nguồn"
            )
        if clip.scene_id:
            scene = next(
                (item for item in asset.scenes if item.id == clip.scene_id), None
            )
            if scene is None:
                raise ValueError(
                    f"{where}.scene_id: cảnh không thuộc tư liệu {asset.name!r}"
                )
        validate_layers(clip.layers, f"{where}.layers")

    for field in ("voice_id", "music_id"):
        asset_id = getattr(project, field)
        if not asset_id:
            continue
        asset = linked_asset(asset_id, field)
        if field == "voice_id" and (
            asset.role != "voice" or asset.media not in {"audio", "video"}
        ):
            raise ValueError(f"voice_id: phải trỏ tới tư liệu voice có audio")
        if field == "music_id" and (
            asset.role != "music"
            or asset.media not in {"audio", "video"}
            or not asset.has_audio
        ):
            raise ValueError(f"music_id: phải trỏ tới tư liệu nhạc có audio")

    validate_layers(project.template.layers, "template.layers")
    for index, layers in enumerate(project.template.slot_layers):
        validate_layers(layers, f"template.slot_layers[{index}]")
    if (
        len(project.template.slot_layers) > len(project.template.slot_durations)
        and project.template.slot_durations
    ):
        raise ValueError("template.slot_layers: số lớp theo ô vượt số nhịp template")
    return project


def validate_project_files(project: Project, project_root: Path) -> None:
    """Check physical asset files separately from JSON structure validation."""
    assets = {asset.id: asset for asset in project.assets}
    referenced = {clip.asset_id for clip in project.clips}
    referenced.update(
        asset_id for asset_id in (project.voice_id, project.music_id) if asset_id
    )
    for layers in [
        project.template.layers,
        *project.template.slot_layers,
        *(clip.layers for clip in project.clips),
    ]:
        referenced.update(
            layer.asset_id
            for layer in layers
            if layer.kind == "image" and layer.asset_id
        )
    referenced.update(asset.id for asset in project.assets if not asset.deleted)
    for asset_id in sorted(referenced):
        asset = assets[asset_id]
        path = (project_root / "assets" / asset.filename).resolve()
        root = (project_root / "assets").resolve()
        if path.parent != root:
            raise ValueError(
                f"assets[{asset.name}].filename: đường dẫn tư liệu không hợp lệ"
            )
        if not path.is_file():
            raise FileNotFoundError(
                f"assets[{asset.name}].filename: thiếu file {asset.filename}"
            )
