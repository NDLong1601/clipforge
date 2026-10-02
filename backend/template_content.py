"""Resolve reusable product fields without copying the reference's claims."""

import re
import json
from pathlib import Path


def bind_placeholders(template):
    aliases = {
        "TÊN SẢN PHẨM": "product_name",
        "MÔ TẢ SẢN PHẨM": "product_description",
        "KHÁM PHÁ SẢN PHẨM": "cta",
    }
    for layers in [template.layers, *template.slot_layers]:
        for layer in layers:
            if layer.kind == "text" and layer.content == "static":
                binding = aliases.get(layer.text.strip().upper())
                if binding:
                    layer.content = binding
    return template


def product_values(project):
    sources = [a for a in project.assets if a.role == "source" and not a.deleted]
    source = sources[-1] if sources else None
    name = project.product_name.strip()
    info = (
        project.product_info
        if source and project.product_info.source_id == source.id
        else None
    )
    if not name and info:
        name = info.name.strip()
    if not name and source:
        name = re.sub(r"[_]+", " ", Path(source.name).stem).strip()
    if not name:
        name = project.name
    description = project.product_description.strip() or project.script.strip()
    if not description and info:
        description = info.description.strip()
    if not description and source:
        description = source.tags.strip()
    return {
        "product_name": name,
        "product_description": description,
        "cta": project.product_cta,
    }


def layer_text(project, layer, clip):
    values = product_values(project)
    if layer.content != "static":
        return values[layer.content]
    text = layer.text
    for key, value in {
        **values,
        "title": clip.title,
        "project": project.name,
        "caption": clip.caption,
    }.items():
        text = text.replace("{" + key + "}", value)
    return text


def infer_product(project, job):
    """Optional enrichment based on the new source, never on the sample's claims."""
    from .providers import ai_json
    from .models import ProductInfo
    from .store import project_dir

    sources = [a for a in project.assets if a.role == "source" and not a.deleted]
    if not sources or project.mode != "template":
        return project
    source = sources[-1]
    if project.product_info.source_id == source.id or (
        project.product_name and project.product_description
    ):
        return project
    groups = [project.template.layers, *project.template.slot_layers]
    if not any(
        layer.content in ("product_name", "product_description")
        or "{product_" in layer.text
        for group in groups
        for layer in group
        if layer.kind == "text"
    ):
        return project
    paths = [
        project_dir(project.id) / "thumbs" / scene.thumbnail
        for scene in source.scenes[:2]
        if scene.thumbnail
        and (project_dir(project.id) / "thumbs" / scene.thumbnail).is_file()
    ]
    prompt = (
        "Xác định tên và mô tả sản phẩm MỚI từ tư liệu/kịch bản sau. Chỉ dùng chữ nhãn đọc được "
        "và thông tin đã được cung cấp. Không dùng tên/mô tả trong template mẫu; không suy đoán "
        "thương hiệu, công dụng y tế, giá hay ưu đãi. Nếu không xác định được tên, trả name rỗng. "
        'Mô tả tối đa 3 câu ngắn, bằng tiếng Việt. Trả JSON {"name":"...","description":"..."}. '
        + json.dumps(
            {
                "source": source.name,
                "tags": source.tags,
                "script": project.script,
                "scenes": [scene.tags for scene in source.scenes[:6]],
            },
            ensure_ascii=False,
        )
    )
    try:
        result = ai_json(prompt, paths, job=job)
        info = ProductInfo(
            source_id=source.id,
            name=result.get("name", ""),
            description=result.get("description", ""),
        )
        if info.name or info.description:
            project.product_info = info
    except (ValueError, TypeError, AttributeError):
        if job:
            job.check()
    return project
