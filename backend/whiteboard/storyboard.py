"""AI storyboard and illustration generation for hand-drawn projects.

These replace the creative steps the kit leaves to Codex: split the voice
into visual beats with short keywords, design one story character, generate
a locked reference sheet and draw every character scene from that reference.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re

import httpx
from PIL import Image

from .. import media, planner, providers, store
from ..errors import ProviderOutcomeUnknown
from ..job_inputs import endpoint_id
from ..models import WBScene, WBTitle, uid

# Drawing treatments from the kit's story-character-presets.json.
STYLES = {
    "bold-doodle": "Playful thick irregular marker, exaggerated silhouette, simple facial shapes, sparse bright flat accents",
    "editorial-ink": "Expressive editorial caricature, variable ink contour, sharp eyebrows and restrained flat washes",
    "retro-pencil": "Loose pencil caricature, a few visible sketch strokes, warm muted accents, clean readable large shapes",
    "comic-flat": "Hand-drawn comic character, bold outer contours, angular expressive poses, limited flat color panels",
    "minimal-sketch": "Economical felt-tip sketch, simplified silhouette, distinctive few facial features, ample negative space",
}
GENDER = {"nam": "male", "nu": "female"}
SCENE_SECONDS = (2.0, 6.5)
MAX_TITLE = 28
client_factory = httpx.Client  # Tests replace the Images API transport.


def _clean_title(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip(" .,;:-")
    return text[:MAX_TITLE].strip()


def _cues(project, duration):
    cues = [cue for cue in project.cues if cue.text.strip() and cue.start < duration]
    if not cues:
        cues = planner.estimated_cues(project.script, duration)
    if not cues:
        raise ValueError("Cần kịch bản hoặc phụ đề của giọng đọc để lập storyboard.")
    return cues


def _heuristic(cues, duration):
    """Group cues into ~2-6.5 s beats; the first words become the keyword."""
    groups, current = [], []
    for cue in cues:
        current.append(cue)
        span = current[-1].end - current[0].start
        if span >= SCENE_SECONDS[0] and (span >= 3.5 or cue.text.rstrip().endswith((".", "!", "?", "…"))):
            groups.append(current)
            current = []
    if current:
        if groups and current[-1].end - current[0].start < SCENE_SECONDS[0]:
            groups[-1].extend(current)
        else:
            groups.append(current)
    plan = []
    for group in groups:
        narration = " ".join(cue.text.strip() for cue in group)
        words = narration.split()
        plan.append({"start": group[0].start, "end": group[-1].end, "narration": narration,
                     "titles": [_clean_title(" ".join(words[:3]).upper())] if words else [],
                     "illustration": narration, "has_character": True, "product": False})
    return plan


def _prompt(project, cues, duration):
    board = project.whiteboard
    lines = "\n".join(f"{index}. [{cue.start:.2f}-{cue.end:.2f}] {cue.text.strip()}" for index, cue in enumerate(cues))
    product = (project.product_name or project.product_info.name or "").strip()
    return f"""Lập storyboard cho video vẽ tay (whiteboard) tiếng Việt dài {duration:.1f} giây.
Người kể: {'nam' if board.case == 'nam' else 'nữ'}. Nhân vật trong tranh là {GENDER[board.case]}.
Phong cách vẽ: {STYLES.get(board.style, board.style)}.
Sản phẩm: {product or 'không nêu'}. {project.product_description.strip()[:400]}
Mô tả nhân vật hiện có: {board.character_description.strip() or 'chưa có, hãy thiết kế mới theo giọng kể'}.

Các câu thoại có mốc thời gian (chỉ số. [bắt đầu-kết thúc] lời):
{lines}

Quy tắc:
- Chia theo từng ý của lời, mỗi cảnh 2-6 giây, dùng chỉ số câu liên tiếp, phủ hết từ câu 0 đến câu {len(cues) - 1}.
- Hình là nội dung chính; mỗi cảnh một tình huống/ẩn dụ đời thường mới, biếm họa thân thiện.
- titles: 1-2 từ khóa tiếng Việt viết HOA, tối đa {MAX_TITLE} ký tự, không đặt cả câu.
- illustration: mô tả tranh bằng tiếng Anh (tư thế, biểu cảm, đồ vật), không có chữ trong tranh, không vẽ bao bì sản phẩm.
- has_character: true khi cảnh cần nhân vật chính. product: true chỉ khi lời đang nói về sản phẩm.
- character: mô tả bằng tiếng Anh ngoại hình cố định của nhân vật (tuổi, mặt, tóc, trang phục, tỉ lệ), giữ nguyên nếu đã có.

Trả JSON: {{"character": "...", "scenes": [{{"from": 0, "to": 1, "titles": ["..."], "accent": 1,
"illustration": "...", "has_character": true, "product": false}}]}}
accent là chỉ số title tô màu nhấn (hoặc -1)."""


def _from_ai(result, cues, duration):
    rows = result.get("scenes") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("AI không trả danh sách cảnh")
    plan, expected = [], 0
    for row in sorted((item for item in rows if isinstance(item, dict)), key=lambda item: int(item.get("from", 0))):
        first, last = int(row.get("from", expected)), int(row.get("to", row.get("from", expected)))
        first, last = max(first, expected), min(max(first, last), len(cues) - 1)
        if first > last:
            continue
        if first > expected and plan:
            plan[-1]["end"] = cues[first - 1].end
            plan[-1]["narration"] += " " + " ".join(cue.text.strip() for cue in cues[expected:first])
        titles = [_clean_title(item) for item in row.get("titles", []) if _clean_title(item)][:2]
        plan.append({"start": cues[first].start, "end": cues[last].end,
                     "narration": " ".join(cue.text.strip() for cue in cues[first:last + 1]),
                     "titles": titles, "accent": row.get("accent", -1),
                     "illustration": str(row.get("illustration", ""))[:1500],
                     "has_character": bool(row.get("has_character", True)), "product": bool(row.get("product", False))})
        expected = last + 1
    if not plan:
        raise ValueError("AI không trả cảnh hợp lệ")
    if expected < len(cues):
        plan[-1]["end"] = cues[-1].end
        plan[-1]["narration"] += " " + " ".join(cue.text.strip() for cue in cues[expected:])
    return plan


def plan(project, use_ai, job):
    board = project.whiteboard
    job.start_step("storyboard")
    job.update(10, "Đọc lời thoại để chia cảnh")
    from .engine import _voice
    _, duration = _voice(project)
    cues = _cues(project, duration)
    rows = None
    if use_ai:
        job.update(30, "AI đang lập storyboard và thiết kế nhân vật")
        result = providers.ai_json(_prompt(project, cues, duration), job=job,
                                   system="Bạn là biên kịch video vẽ tay. Trả về JSON hợp lệ, không markdown.")
        rows = _from_ai(result, cues, duration)
        character = str(result.get("character", "")).strip()
        if character and not board.character_description.strip():
            board.character_description = character[:1500]
    else:
        rows = _heuristic(cues, duration)
    scenes = []
    for index, row in enumerate(rows):
        start = 0.0 if index == 0 else round(row["start"], 3)
        end = duration if index == len(rows) - 1 else round(rows[index + 1]["start"], 3)
        if end - start < 0.4:
            continue
        accent = row.get("accent", -1)
        titles = [WBTitle(text=text, accent=i == accent) for i, text in enumerate(row["titles"])]
        scenes.append(WBScene(id=f"s{index + 1:02d}-{uid()[:6]}", start=start, end=round(end, 3),
                              narration=row["narration"][:1000], image_prompt=row["illustration"][:2000],
                              titles=titles, has_character=row["has_character"],
                              product=row["product"] and bool(board.product_id)))
    if not scenes:
        raise ValueError("Không chia được cảnh từ lời thoại.")
    board.scenes = scenes
    project.mode = "whiteboard"
    if not board.character_description.strip():
        board.character_description = default_character(board.case)
    job.update(95, f"Đã chia {len(scenes)} cảnh")
    return project


def default_character(case):
    if case == "nu":
        return ("Vietnamese woman in her early 30s, round friendly face, expressive eyes, shoulder-length black hair "
                "with a side clip, mustard cardigan over a white tee, slim proportions with a slightly large head")
    return ("Vietnamese man in his mid 30s, oval face, thick eyebrows, short neat black hair, rust-colored polo shirt, "
            "dark trousers, slightly large head on a lean body")


def style_text(board):
    drawing = STYLES.get(board.style, STYLES["editorial-ink"])
    return (f"Clean hand-drawn Vietnamese explainer illustration. {drawing}. Friendly everyday satire, large readable "
            "shapes, gentle comic exaggeration, clear dark ink outlines with few details, flat colors limited to ink "
            "#2B2A28, accent #AC5437, muted #716C63 and dusty blue #7D9DA8. Uniform plain flat "
            f"{board.background} background filling the whole image. Keep a 5% safe margin and generous blank gaps "
            "between independent objects. No text, letters, numbers, logos, watermarks, medical textbook diagrams, "
            "photo or 3D.")


def reference_prompt(board):
    return ("Character reference sheet for a hand-drawn explainer video. One " + GENDER[board.case] + " story "
            "character: full body front view and a larger clear face, plus two small expressions of the same "
            f"character. {board.character_description.strip() or default_character(board.case)}. {style_text(board)}")


def scene_prompt(board, scene, with_reference):
    parts = [style_text(board)]
    if scene.has_character:
        description = board.character_description.strip() or default_character(board.case)
        if with_reference:
            parts.append("Use the supplied image as the LOCKED story character and drawing-style reference: same face, "
                         "hair, apparent age, clothing, proportions, line treatment and palette.")
        parts.append(f"Main character ({GENDER[board.case]}): {description}.")
    else:
        parts.append("No main character in this scene; keep the same line work and palette.")
    if scene.product:
        parts.append("Do not draw any product packaging or bottle; the real product photo is added separately.")
    parts.append("Scene: " + (scene.image_prompt.strip() or scene.narration.strip()))
    return " ".join(parts)


def _route(settings):
    profiles = [item for item in settings.ai_profiles if item.enabled and item.api_key]
    chosen = next((item for item in profiles if item.id == settings.image_profile_id), None)
    if chosen is None:
        chosen = next((item for item in profiles if item.id == settings.active_ai_profile_id
                       and item.provider != "gemini"), None)
    if chosen is None:
        chosen = next((item for item in profiles if item.provider != "gemini"), None)
    if chosen:
        return chosen.provider, providers.ai_endpoint(chosen), chosen.api_key
    if settings.ai_key:
        return "openai", settings.ai_base_url.rstrip("/"), settings.ai_key
    raise ValueError("Cần cấu hình API hỗ trợ tạo ảnh (OpenAI hoặc tương thích). Mở Cài đặt API để chọn.")


def _size(model, aspect, product):
    if model.startswith("dall-e-2"):
        return "1024x1024"
    if aspect == "16:9" and not product:
        return "1792x1024" if model.startswith("dall-e-3") else "1536x1024"
    return "1024x1024"


def _decode(response, client):
    data = response.json()["data"][0]
    if data.get("b64_json"):
        return base64.b64decode(data["b64_json"])
    if data.get("url"):
        fetched = client.get(data["url"], timeout=120)
        fetched.raise_for_status()
        return fetched.content
    raise ValueError("Dịch vụ ảnh không trả dữ liệu ảnh")


def generate(project, prompt, size, reference, job, settings):
    """One provider image with job bookkeeping; returns PNG bytes."""
    provider, base, key = _route(settings)
    model = settings.image_model
    body = {"model": model, "prompt": prompt[:3900], "size": size, "n": 1}
    if model.startswith("gpt-image"):
        body["quality"] = settings.image_quality
    elif model.startswith("dall-e"):
        body["response_format"] = "b64_json"
    use_edit = reference is not None and provider != "gemini"
    reference_hash = hashlib.sha256(reference).hexdigest() if use_edit else None
    fingerprint = hashlib.sha256(json.dumps({"endpoint_id": endpoint_id(base), "body": body, "edit": use_edit,
                                             "reference": reference_hash, "version": 1},
                                            sort_keys=True).encode()).hexdigest()
    cache = store.project_dir(project.id) / "cache" / "whiteboard-images"
    cached_path = cache / f"{fingerprint}.png"
    call_id, cached = job.begin_provider_call(fingerprint, provider, model, "image")
    if cached is not None:
        if cached_path.is_file() and media.file_sha256(cached_path) == cached.get("sha256"):
            return cached_path.read_bytes()
        message = "Ảnh AI đã tạo trước đó bị thiếu; cần xác nhận tạo lại (có thể phát sinh phí)."
        job.mark_provider_unknown(call_id, message, invalid_result=True)
        raise ProviderOutcomeUnknown(message)
    headers = {"Authorization": "Bearer " + key}
    try:
        with client_factory(timeout=httpx.Timeout(240, connect=10)) as client:
            if use_edit:
                files = {"image": ("reference.png", reference, "image/png")}
                form = {name: str(value) for name, value in body.items() if name != "response_format"}
                if "response_format" in body:
                    form["response_format"] = body["response_format"]
                response = client.post(base + "/images/edits", headers=headers, data=form, files=files)
            else:
                response = client.post(base + "/images/generations", headers=headers, json=body)
            if response.is_error:
                job.finish_provider_call(call_id, "failed_known", response.status_code)
                providers.checked(response)
            content = _decode(response, client)
    except httpx.RequestError:
        message = "Không xác định được kết quả tạo ảnh. Gửi lại có thể phát sinh phí thêm."
        job.mark_provider_unknown(call_id, message)
        raise ProviderOutcomeUnknown(message) from None
    except (ValueError, KeyError, IndexError, TypeError, httpx.HTTPStatusError) as exc:
        if isinstance(exc, ValueError) and "HTTP" in str(exc):
            raise
        job.finish_provider_call(call_id, "invalid_response", response.status_code)
        raise ValueError("Dịch vụ ảnh trả phản hồi không hợp lệ.") from None
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, "PNG")
    except OSError:
        job.finish_provider_call(call_id, "invalid_response", response.status_code)
        raise ValueError("Dịch vụ ảnh trả file không giải mã được.") from None
    cache.mkdir(parents=True, exist_ok=True)
    temporary = cached_path.with_name(f".{cached_path.name}.{job.id}.tmp")
    temporary.write_bytes(buffer.getvalue())
    os.replace(temporary, cached_path)
    job.complete_provider_call(call_id, {"artifact_path": cached_path.resolve().relative_to(store.ROOT.resolve()).as_posix(),
                                         "sha256": media.file_sha256(cached_path)})
    job.check()
    return cached_path.read_bytes()


def _add_illustration(project, content, name, job):
    path = store.project_dir(project.id) / "assets" / f"wb_{job.id}_{uid()[:8]}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    asset = media.register(project.id, path, name, "illustration", job)
    project.assets.append(asset)
    return asset


def images(project, payload, job):
    board = project.whiteboard
    settings = providers.effective_settings(job)
    _route(settings)
    job.start_step("generate-images")
    wanted = set(payload.get("scene_ids") or [])
    targets = [] if payload.get("only_character") else [
        scene for scene in board.scenes
        if (not wanted or scene.id in wanted) and (not payload.get("missing_only", True) or not scene.image_id)]
    need_reference = payload.get("character") or (
        any(scene.has_character for scene in targets) and not board.character_reference_id)
    total = len(targets) + int(bool(need_reference))
    if not total:
        raise ValueError("Không có cảnh nào cần tạo ảnh. Bỏ chọn 'chỉ cảnh còn thiếu' để vẽ lại.")
    done = 0
    if need_reference:
        job.update(5, "Tạo ảnh nhân vật khóa cho cả video")
        content = generate(project, reference_prompt(board), "1024x1024", None, job, settings)
        board.character_reference_id = _add_illustration(project, content, "Nhân vật khóa.png", job).id
        done += 1
    reference = None
    if board.character_reference_id:
        asset = store.get_asset(project, board.character_reference_id)
        with Image.open(store.asset_path(project.id, asset)) as image:
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, "PNG")
            reference = buffer.getvalue()
    for scene in targets:
        index = board.scenes.index(scene) + 1
        job.update(5 + 90 * done / total, f"Vẽ tranh cảnh {index}/{len(board.scenes)}")
        with_reference = scene.has_character and reference is not None
        content = generate(project, scene_prompt(board, scene, with_reference),
                           _size(settings.image_model, board.aspect, scene.product),
                           reference if with_reference else None, job, settings)
        asset = _add_illustration(project, content, f"Cảnh {index}.png", job)
        scene.image_id, scene.crop, scene.layout = asset.id, None, None
        done += 1
    project.mode = "whiteboard"
    return project
