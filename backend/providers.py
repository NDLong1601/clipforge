import base64, json, re, os, subprocess, html, hashlib, time
from pathlib import Path
import httpx
from .models import Cue, Asset, Template, uid, AIProposal, SceneLabels, TextRegion
from .store import settings, project_dir, asset_path
from .media import probe, run_ff, extract_audio, register


def checked(response):
    if response.is_error:
        # Don't echo provider response bodies: they may include credentials or request data.
        raise ValueError(
            f"Dịch vụ AI trả lỗi HTTP {response.status_code}. Kiểm tra API key, model, hạn mức và địa chỉ API."
        )
    return response


def ai_candidates(s, profile_id=None, allow_fallback=None):
    profiles = [p for p in s.ai_profiles if p.enabled]
    if profile_id:
        selected = next((p for p in profiles if p.id == profile_id), None)
        if selected is None:
            raise ValueError("Cấu hình API đã chọn không tồn tại hoặc đang tắt")
    else:
        selected = next((p for p in profiles if p.id == s.active_ai_profile_id), None)
        if selected is None and profiles:
            selected = profiles[0]
    if selected is None:
        raise ValueError(
            "Chưa có cấu hình AI đang bật. Mở Cài đặt API để thêm khóa và model."
        )
    fallback = s.ai_auto_fallback if allow_fallback is None else allow_fallback
    return (
        [selected] + [p for p in profiles if p.id != selected.id]
        if fallback
        else [selected]
    )


def ai_endpoint(profile):
    if profile.provider == "openai":
        return "https://api.openai.com/v1"
    if profile.provider == "gemini":
        return "https://generativelanguage.googleapis.com/v1beta/openai"
    return profile.base_url.rstrip("/")


def _endpoint_id(profile):
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(ai_endpoint(profile))
    endpoint = urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            parts.query,
            "",
        )
    )
    return hashlib.sha256(endpoint.encode()).hexdigest()


def _label_cache_key(provider, model, endpoint_id, instruction, image_hash):
    material = json.dumps(
        ["scene-label-v3", provider, model, endpoint_id, instruction, image_hash],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def audio_connection(s):
    candidates = [
        p
        for p in s.ai_profiles
        if p.enabled and p.provider in ("openai", "compatible") and p.api_key
    ]
    chosen = next((p for p in candidates if p.id == s.active_ai_profile_id), None)
    if chosen is None and candidates:
        chosen = candidates[0]
    if chosen:
        return ai_endpoint(chosen), chosen.api_key
    if s.ai_key:
        return s.ai_base_url.rstrip("/"), s.ai_key
    raise ValueError(
        "Cần khóa OpenAI hoặc API tương thích hỗ trợ Speech. Gemini trong danh sách AI chỉ dùng cho biên tập."
    )


def _usage(response):
    try:
        raw = response.json().get("usage") or {}
        usage = {
            key: raw[key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(raw.get(key), int) and raw[key] >= 0
        }
        return usage or None
    except (ValueError, AttributeError, TypeError):
        return None


def _record_provider_call(
    job, profile, fallback, attempt, started, outcome, status_code=None, usage=None
):
    if job:
        job.record_provider_call(
            {
                "profile_id": profile.id,
                "name": profile.name,
                "provider": profile.provider,
                "model": profile.model,
                "fallback": bool(fallback),
                "attempt": attempt,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "outcome": outcome,
                "status_code": status_code,
                "usage": usage,
            }
        )


def ai_json(
    prompt,
    images=None,
    system="Bạn là trợ lý biên tập video. Trả về JSON hợp lệ, không markdown.",
    profile_id=None,
    allow_fallback=None,
    return_route=False,
    job=None,
):
    s = settings()
    candidates = ai_candidates(s, profile_id, allow_fallback)
    content = [{"type": "text", "text": prompt}]
    for path in images or []:
        if job:
            job.check()
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,"
                    + base64.b64encode(Path(path).read_bytes()).decode(),
                    "detail": "low",
                },
            }
        )
    failures = []
    for profile in candidates:
        if job:
            job.check()
        url = ai_endpoint(profile)
        if not profile.api_key and not url.startswith(
            ("http://localhost", "http://127.0.0.1")
        ):
            failures.append(f"{profile.name} ({profile.model}): thiếu API key")
            continue
        body = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if profile.provider == "openai":
            body["response_format"] = {"type": "json_object"}
        attempt = 0
        while attempt < 2:
            if job:
                job.check()
            attempt += 1
            started = time.monotonic()
            response = None
            usage = None
            try:
                with httpx.Client(timeout=httpx.Timeout(90, connect=8)) as client:
                    response = client.post(
                        url + "/chat/completions",
                        headers=(
                            {"Authorization": "Bearer " + profile.api_key}
                            if profile.api_key
                            else {}
                        ),
                        json=body,
                    )
                # A sent provider request may finish after cancellation. Do not issue
                # another provider request or apply its result once cancellation lands.
                usage = None if response.is_error else _usage(response)
                if job:
                    try:
                        job.check()
                    except Exception:
                        _record_provider_call(
                            job,
                            profile,
                            profile.id != candidates[0].id,
                            attempt,
                            started,
                            "cancelled",
                            response.status_code,
                            usage,
                        )
                        raise
                if response.is_error:
                    transient = (
                        response.status_code in (408, 425, 429)
                        or response.status_code >= 500
                    )
                    _record_provider_call(
                        job,
                        profile,
                        profile.id != candidates[0].id,
                        attempt,
                        started,
                        "http_error",
                        response.status_code,
                    )
                    failures.append(
                        f"{profile.name} ({profile.model}): HTTP {response.status_code}"
                    )
                    if transient and attempt < 2:
                        if job:
                            job.check()
                        time.sleep(0.35 * attempt)
                        continue
                    break
                answer = response.json()["choices"][0]["message"]["content"]
                if not isinstance(answer, str):
                    raise ValueError("phản hồi không có nội dung")
                answer = answer.strip()
                if answer.startswith("```"):
                    answer = re.sub(
                        r"^\`\`\`(?:json)?\s*|\s*\`\`\`$", "", answer, flags=re.I
                    ).strip()
                result = json.loads(answer)
                if not isinstance(result, dict):
                    raise ValueError("phản hồi không phải JSON object")
                elapsed = round((time.monotonic() - started) * 1000)
                previous_calls = job.provider_calls if job else []
                total_usage = {}
                for call in previous_calls:
                    for key, value in (call.get("usage") or {}).items():
                        if isinstance(value, int):
                            total_usage[key] = total_usage.get(key, 0) + value
                for key, value in (usage or {}).items():
                    total_usage[key] = total_usage.get(key, 0) + value
                route = {
                    "id": profile.id,
                    "name": profile.name,
                    "provider": profile.provider,
                    "model": profile.model,
                    "endpoint_id": _endpoint_id(profile),
                    "fallback_used": profile.id != candidates[0].id,
                    "elapsed_ms": elapsed,
                    "total_elapsed_ms": sum(
                        call.get("elapsed_ms", 0) for call in previous_calls
                    )
                    + elapsed,
                    "call_count": len(previous_calls) + 1,
                    "usage": usage,
                    "total_usage": total_usage or None,
                }
                _record_provider_call(
                    job,
                    profile,
                    route["fallback_used"],
                    attempt,
                    started,
                    "success",
                    response.status_code,
                    usage,
                )
                return (result, route) if return_route else result
            except httpx.TimeoutException:
                reason = "hết thời gian chờ"
                _record_provider_call(
                    job,
                    profile,
                    profile.id != candidates[0].id,
                    attempt,
                    started,
                    "timeout",
                )
                retry = True
            except httpx.RequestError:
                reason = "lỗi mạng"
                _record_provider_call(
                    job,
                    profile,
                    profile.id != candidates[0].id,
                    attempt,
                    started,
                    "network_error",
                )
                retry = True
            except (ValueError, KeyError, TypeError, IndexError):
                reason = "phản hồi JSON không hợp lệ"
                _record_provider_call(
                    job,
                    profile,
                    profile.id != candidates[0].id,
                    attempt,
                    started,
                    "invalid_response",
                    response.status_code if response is not None else None,
                    usage,
                )
                retry = False
            failures.append(f"{profile.name} ({profile.model}): {reason}")
            if job:
                job.check()
            if not retry or attempt >= 2:
                break
            time.sleep(0.35 * attempt)
    raise ValueError("Không kết nối được AI qua các cấu hình: " + "; ".join(failures))


def _normalized_text_labels(result):
    """Accept the requested 1000-unit vision grid or already normalized boxes."""
    import copy

    result = copy.deepcopy(result)
    for entry in result.get("scenes", []):
        if not isinstance(entry, dict):
            continue
        for region in entry.get("text_regions", []):
            if not isinstance(region, dict) or set(region) != {"x", "y", "w", "h"}:
                continue
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in region.values()
            ):
                raise ValueError("AI trả tọa độ vùng chữ không hợp lệ.")
            if any(value > 1 for value in region.values()):
                for key in region:
                    region[key] /= 1000
    return result


def label_scenes(p, job):
    items = [
        (a, sc)
        for a in p.assets
        if a.role == "source" and not a.deleted
        for sc in a.scenes
        if not sc.ai_labeled or (p.source_text_mode != "off" and not sc.text_analyzed)
    ]
    instruction = (
        "Mỗi ảnh tương ứng một cảnh theo thứ tự. Mô tả tiếng Việt ngắn, đối tượng, hành động, "
        "bối cảnh. Không suy đoán danh tính. Đồng thời tìm chữ/phụ đề đồ họa được chèn lên video; "
        "không đánh dấu chữ trên đồ vật thật hoặc biển hiệu. Mỗi vùng là hình chữ nhật ôm trọn chữ, "
        "thêm lề nhỏ, tọa độ x,y,w,h theo lưới chuẩn hóa 0–1000 trên toàn bộ ảnh nguồn, "
        "(0,0) là góc trên trái, (1000,1000) là góc dưới phải, KHÔNG dùng pixel. "
        "Không có chữ chèn thì text_regions=[]. "
        'JSON {"scenes":[{"index":0,"tags":"...","text_regions":[{"x":100,"y":800,"w":800,"h":120}]}]}.'
    )
    enabled = ai_candidates(settings())
    pending = []
    cache_root = project_dir(p.id).parent.parent / "cache" / "ai-labels"
    cache_root.mkdir(parents=True, exist_ok=True)
    for asset, scene in items:
        job.check()
        path = project_dir(p.id) / "thumbs" / scene.thumbnail
        try:
            image_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            pending.append((asset, scene, path, None))
            continue
        cached = None
        for profile in enabled:
            key = _label_cache_key(
                profile.provider,
                profile.model,
                _endpoint_id(profile),
                instruction,
                image_hash,
            )
            try:
                value = json.loads(
                    (cache_root / f"{key}.json").read_text(encoding="utf-8")
                )
                if (
                    value.get("image_hash") == image_hash
                    and isinstance(value.get("tags"), str)
                    and value["tags"].strip()
                ):
                    regions = [
                        TextRegion.model_validate(region)
                        for region in value.get("text_regions", [])
                    ]
                    cached = (value["tags"], regions, "text_regions" in value)
                    break
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        if cached is None:
            pending.append((asset, scene, path, image_hash))
        else:
            scene.tags = cached[0][:1500]
            scene.ai_labeled = True
            scene.text_regions = cached[1]
            scene.text_analyzed = cached[2]
    for start in range(0, len(pending), 6):
        job.update(35 + 50 * start / max(1, len(pending)), "AI đang mô tả các cảnh")
        group = pending[start : start + 6]
        result, route = ai_json(
            instruction, [item[2] for item in group], job=job, return_route=True
        )
        labels = SceneLabels.model_validate(_normalized_text_labels(result)).scenes
        if {entry["index"] for entry in labels} != set(range(len(group))):
            raise ValueError(
                "AI trả thiếu hoặc sai chỉ số cảnh. Hãy chạy lại phân tích nhãn."
            )
        for entry in labels:
            asset, scene, path, image_hash = group[entry["index"]]
            tags = entry["tags"].strip()[:1500]
            scene.tags = tags
            scene.ai_labeled = True
            if "text_regions" in entry:
                scene.text_regions = [
                    TextRegion.model_validate(region)
                    for region in entry["text_regions"]
                ]
                scene.text_analyzed = True
            if image_hash:
                key = _label_cache_key(
                    route["provider"],
                    route["model"],
                    route["endpoint_id"],
                    instruction,
                    image_hash,
                )
                from .store import atomic

                value = {"image_hash": image_hash, "tags": tags}
                if "text_regions" in entry:
                    value["text_regions"] = entry["text_regions"]
                atomic(cache_root / f"{key}.json", value)
    return p


def infer_template(p, asset, job):
    from .template_utils import compressed_slot_durations

    scenes = asset.scenes
    picks = scenes[:: max(1, len(scenes) // 6)][:6]
    prompt = """Phân tích bố cục video mẫu từ các ảnh theo thời gian. Tạo template dựng lại với nội dung mới, giữ hình khối, vị trí khung, text, màu và biểu tượng đơn giản. Không giả vờ tách được logo/font gốc. Trả JSON theo schema:
{"name":"Tên mẫu","background":"#111111","viewport":{"x":0,"y":0,"w":1,"h":1},"transition":"cut","layers":[{"kind":"text|rect|circle","x":0.07,"y":0.08,"w":0.86,"h":0.12,"text":"{title}","size":54,"color":"#ffffff","background":"#000000","opacity":1,"animation":"none"}],"caption":{"enabled":true,"font_size":52,"color":"#ffffff","highlight":"#b5f36d","bottom":0.18,"words_per_line":6,"karaoke":false},"notes":"Các chi tiết cần người dùng duyệt"}.
Tọa độ 0..1; x+w,y+h <=1. size tính theo chiều rộng 1080. {title} sẽ thay bằng tiêu đề từng đoạn; {project} là tên dự án. Với tên sản phẩm dùng {product_name}, mô tả dùng {product_description}, lời kêu gọi dùng {cta}. Không giữ nguyên câu chữ nội dung mẫu. Nếu mẫu có nhiều bố cục, thêm slot_layers là các mảng layers theo thứ tự ảnh. Chỉ dùng trường schema trên."""
    result = ai_json(
        prompt, [project_dir(p.id) / "thumbs" / x.thumbnail for x in picks], job=job
    )
    result["slot_durations"] = compressed_slot_durations(asset)
    if len(scenes) > 100:
        note = "Đã gộp nhịp liên tiếp để vừa giới hạn 100 ô; toàn bộ cảnh gốc và thời lượng video mẫu vẫn được giữ."
        result["notes"] = (str(result.get("notes", "")).strip() + " " + note).strip()
    try:
        return Template.model_validate(result)
    except Exception as e:
        raise ValueError(
            "AI trả bố cục không hợp lệ. Hãy thử lại hoặc sửa mẫu thủ công."
        ) from e


def sentences(text):
    return [
        s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text.strip()) if s.strip()
    ]


def speak_sentence(text, dest, s, job):
    job.check()
    if s.tts_provider == "openai":
        base_url, key = audio_connection(s)
        with httpx.Client(timeout=120) as client:
            r = checked(
                client.post(
                    base_url + "/audio/speech",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "model": s.tts_model,
                        "voice": s.tts_voice,
                        "input": text,
                        "response_format": "wav",
                        "speed": s.tts_speed,
                    },
                )
            )
            dest.write_bytes(r.content)
    elif s.tts_provider == "azure":
        if not s.azure_key:
            raise ValueError("Cần Azure Speech key để tạo giọng tiếng Việt")
        if not re.fullmatch("[a-z0-9-]+", s.azure_region):
            raise ValueError("Azure region không hợp lệ")
        ssml = f'<speak version="1.0" xml:lang="vi-VN"><voice name="{html.escape(s.azure_voice,quote=True)}"><prosody rate="{s.tts_speed:.2f}">{html.escape(text)}</prosody></voice></speak>'
        with httpx.Client(timeout=120) as client:
            r = checked(
                client.post(
                    f"https://{s.azure_region}.tts.speech.microsoft.com/cognitiveservices/v1",
                    headers={
                        "Ocp-Apim-Subscription-Key": s.azure_key,
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                    },
                    content=ssml.encode(),
                )
            )
            dest.write_bytes(r.content)
    else:
        if os.name != "nt":
            raise ValueError("Giọng Windows chỉ dùng trên Windows")
        config = dest.with_suffix(".json")
        config.write_text(
            json.dumps(
                {
                    "text": text,
                    "path": str(dest),
                    "voice": s.windows_voice,
                    "rate": round((s.tts_speed - 1) * 5),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ps = dest.with_suffix(".ps1")
        ps.write_text(
            """param([string]$ConfigPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($cfg.voice) { $speech.SelectVoice($cfg.voice) }
$speech.Rate = $cfg.rate
$speech.SetOutputToWaveFile($cfg.path)
$speech.Speak($cfg.text)
$speech.Dispose()
""",
            encoding="utf-8-sig",
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps),
                str(config),
            ],
            capture_output=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        config.unlink(missing_ok=True)
        ps.unlink(missing_ok=True)
        if result.returncode:
            raise ValueError("Không tạo được giọng Windows. Kiểm tra tên giọng đã cài.")
    job.check()


def synthesize(p, job):
    parts = sentences(p.script)
    if not parts:
        raise ValueError("Nhập kịch bản trước khi tạo giọng")
    if len(p.script) > 6000:
        raise ValueError("Kịch bản tạo giọng tối đa 6.000 ký tự")
    s = settings()
    d = project_dir(p.id) / "cache" / job.id
    d.mkdir(parents=True, exist_ok=True)
    cues = []
    total = 0
    for i, text in enumerate(parts):
        job.update(5 + 75 * i / len(parts), f"Tạo giọng câu {i+1}/{len(parts)}")
        wav = d / f"speech_{i:03d}.wav"
        speak_sentence(text, wav, s, job)
        normalized = d / f"part_{i:03d}.wav"
        run_ff(["-i", wav, "-ar", "48000", "-ac", "2", normalized], job=job)
        duration = probe(normalized)["duration"]
        cues.append(Cue(start=total, end=total + duration, text=text))
        total += duration
    if total > 180:
        raise ValueError("Giọng dài quá 180 giây. Hãy rút gọn kịch bản.")
    (d / "list.txt").write_text(
        "\n".join(f"file 'part_{i:03d}.wav'" for i in range(len(parts))),
        encoding="utf-8",
    )
    out = project_dir(p.id) / "assets" / f"{uid()}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ff(
        ["-f", "concat", "-safe", "0", "-i", "list.txt", "-c:a", "pcm_s16le", out],
        cwd=d,
        job=job,
    )
    a = register(p.id, out, "Giọng đọc AI.wav", "voice", job)
    p.assets.append(a)
    p.voice_id = a.id
    p.cues = cues
    p.cue_timing = "sentence-exact"
    p.cues_edited = False
    p.cues_stale = False
    if total > p.target_duration:
        p.target_duration = min(180, round(total, 2))
        p.warnings = [
            "Giọng dài hơn thời lượng đã chọn; timeline được kéo dài để giữ trọn lời đọc."
        ]
    return p


def _gemini_audio(wav, duration, s, job):
    from urllib.parse import quote

    profiles = [
        profile
        for profile in s.ai_profiles
        if profile.enabled and profile.provider == "gemini" and profile.api_key
    ]
    if not profiles:
        raise ValueError(
            "Thêm API Gemini đang bật hoặc cấu hình nhận dạng OpenAI/Faster Whisper."
        )
    chosen = next(
        (profile for profile in profiles if profile.id == s.active_ai_profile_id),
        profiles[0],
    )
    profiles = (
        [chosen] + [profile for profile in profiles if profile.id != chosen.id]
        if s.ai_auto_fallback
        else [chosen]
    )
    if wav.stat().st_size > 14 * 1024 * 1024:
        raise ValueError(
            "Audio quá lớn để gửi trực tiếp tới Gemini. Rút ngắn audio hoặc dùng Faster Whisper."
        )
    prompt = (
        f"Nghe audio dài {duration:.3f} giây. Chép nguyên văn lời nói bằng ngôn ngữ gốc, "
        "chia thành câu/ý ngắn 1–4 giây với start/end là số giây từ đầu audio. "
        "Giữ khoảng nghỉ, không suy diễn lời không nghe thấy, không chép nhạc hoặc tiếng động thành lời. "
        'Nếu không có lời nói, trả segments=[]. Chỉ trả JSON {"segments":[{"start":0.0,"end":2.0,"text":"..."}]}.'
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": "audio/wav",
                            "data": base64.b64encode(wav.read_bytes()).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "segments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "start": {"type": "NUMBER"},
                                "end": {"type": "NUMBER"},
                                "text": {"type": "STRING"},
                            },
                            "required": ["start", "end", "text"],
                        },
                    }
                },
                "required": ["segments"],
            },
        },
    }
    for index, profile in enumerate(profiles):
        for attempt in range(1, 3):
            job.check()
            started = time.monotonic()
            try:
                with httpx.Client(timeout=240) as client:
                    response = client.post(
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        + quote(profile.model, safe="")
                        + ":generateContent",
                        headers={"x-goog-api-key": profile.api_key},
                        json=payload,
                    )
                job.check()
                _record_provider_call(
                    job,
                    profile,
                    index > 0,
                    attempt,
                    started,
                    "success" if not response.is_error else "http_error",
                    status_code=response.status_code,
                )
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    continue
                checked(response)
                candidates = response.json().get("candidates", [])
                parts = (
                    candidates[0].get("content", {}).get("parts", [])
                    if candidates
                    else []
                )
                result = json.loads(
                    "".join(
                        part.get("text", "")
                        for part in parts
                        if not part.get("thought")
                    )
                )
                entries = result.get("segments")
                if not isinstance(entries, list) or len(entries) > 1000:
                    raise ValueError("Gemini trả mốc audio không hợp lệ.")
                cues = []
                for entry in entries:
                    cue = Cue.model_validate(entry)
                    if not cue.text.strip():
                        continue
                    if (
                        cue.end > duration + 0.15
                        or cue.start >= duration
                        or (cues and cue.start < cues[-1].end - 0.05)
                    ):
                        raise ValueError(
                            "Gemini trả mốc ngoài audio hoặc chồng lấn. Thử nhận dạng lại hoặc dùng Faster Whisper."
                        )
                    cue.end = min(duration, cue.end)
                    cue.text = cue.text.strip()
                    cues.append(cue)
                return cues
            except httpx.RequestError:
                _record_provider_call(
                    job, profile, index > 0, attempt, started, "network_error"
                )
                if attempt == 2 and index == len(profiles) - 1:
                    raise ValueError(
                        "Không kết nối được Gemini để phân tích audio."
                    ) from None
            except (ValueError, KeyError, TypeError, IndexError):
                if index == len(profiles) - 1:
                    raise ValueError(
                        "Gemini không trả lời đọc/mốc audio hợp lệ. Kiểm tra API hoặc thử nhận dạng lại."
                    ) from None
                break


def transcribe(p, asset, job):
    if not asset.has_audio:
        raise ValueError("Tư liệu đã chọn không có âm thanh.")
    s = settings()
    d = project_dir(p.id) / "cache" / job.id
    d.mkdir(parents=True, exist_ok=True)
    wav = d / "audio.wav"
    extract_audio(asset_path(p.id, asset), wav, job)
    job.update(25, "Nhận dạng lời đọc và mốc thời gian")
    provider = s.transcription_provider
    if provider == "auto":
        try:
            audio_connection(s)
            provider = "openai"
        except ValueError:
            provider = "gemini"
    elif provider == "openai":
        # Existing installations with only Gemini can use their current profile.
        try:
            audio_connection(s)
        except ValueError:
            provider = "gemini"
    if provider == "gemini":
        cues = _gemini_audio(wav, asset.duration, s, job)
    elif provider == "local":
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ValueError(
                "Chưa cài faster-whisper. Chạy: pip install faster-whisper. Model sẽ được tải ở lần dùng đầu."
            ) from e
        model = WhisperModel(s.local_whisper_model, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(wav), language=s.language or None, word_timestamps=True
        )
        cues = []
        for segment in segments:
            job.check()
            cues.extend(
                Cue(
                    start=max(0, word.start),
                    end=max(word.start + 0.02, word.end),
                    text=word.word.strip(),
                )
                for word in segment.words
                if word.word.strip()
            )
        job.check()
    else:
        base_url, key = audio_connection(s)
        if wav.stat().st_size > 24 * 1024 * 1024:
            raise ValueError(
                "Âm thanh quá lớn. Chỉ nhận dạng voice của video đầu ra, tối đa khoảng 12 phút."
            )
        with httpx.Client(timeout=240) as client, wav.open("rb") as f:
            r = checked(
                client.post(
                    base_url + "/audio/transcriptions",
                    headers={"Authorization": "Bearer " + key},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={
                        "model": s.transcription_model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "word",
                        "language": s.language,
                    },
                )
            )
        job.check()
        data = r.json()
        words = data.get("words") or data.get("segments") or []
        cues = [
            Cue(
                start=max(0, w["start"]),
                end=max(w["start"] + 0.02, w["end"]),
                text=w.get("word", w.get("text", "")).strip(),
            )
            for w in words
        ]
    if not cues:
        raise ValueError(
            "Không nhận được lời nói trong audio. Chọn file giọng đọc rõ tiếng hoặc đổi bộ nhận dạng."
        )
    if sum(len(cue.text) + 1 for cue in cues) > 12000:
        raise ValueError("Lời đọc vượt giới hạn 12.000 ký tự.")
    job.check()
    p.cues = cues
    p.cue_timing = "audio_segments" if provider == "gemini" else "transcribed"
    p.voice_id = asset.id
    p.cues_edited = False
    p.cues_stale = False
    p.script = " ".join(c.text for c in cues)
    if asset.duration > p.target_duration:
        p.target_duration = min(180, asset.duration)
    return p


def assistant(p, prompt, job=None):
    context = {
        "script": p.script,
        "music_volume": p.music_volume,
        "clips": [c.model_dump() for c in p.clips],
        "sources": [
            {
                "id": a.id,
                "name": a.name,
                "tags": a.tags,
                "scenes": [{"id": scene.id, "tags": scene.tags} for scene in a.scenes],
            }
            for a in p.assets
            if a.role == "source" and not a.deleted
        ],
    }
    raw, route = ai_json(
        "Dựa vào yêu cầu, chỉ đề xuất thay đổi có ích. Không thực thi mã. Trả JSON đúng schema "
        '{"message":"giải thích tiếng Việt","script":null,"music_volume":null,'
        '"clip_changes":[{"id":"clip id","title":"...","caption":"...",'
        '"asset_id":"source id","source_start":0,"duration":2.5}]}. '
        "Chỉ dùng khóa trên và chỉ đưa trường cần sửa; clip_changes luôn là mảng. Không bịa ID "
        "clip, asset hay scene. Không đề xuất sửa cảnh đã khóa. Giữ nguyên lời đọc khi chỉ thay cảnh. Yêu cầu: "
        + prompt
        + "\nDự án: "
        + json.dumps(context, ensure_ascii=False),
        job=job,
        return_route=True,
    )
    try:
        proposal = AIProposal.model_validate(raw)
    except Exception as exc:
        raise ValueError(
            "Đề xuất AI sai schema. Dự án chưa thay đổi; hãy tạo đề xuất mới."
        ) from exc
    clip_ids = [change.id for change in proposal.clip_changes]
    if len(clip_ids) != len(set(clip_ids)):
        raise ValueError(
            "Đề xuất AI lặp ID cảnh. Dự án chưa thay đổi; hãy tạo đề xuất mới."
        )
    clips = {clip.id: clip for clip in p.clips}
    assets = {asset.id: asset for asset in p.assets}
    for change in proposal.clip_changes:
        clip = clips.get(change.id)
        if clip is None:
            raise ValueError(
                "Đề xuất AI chứa ID cảnh không tồn tại. Dự án chưa thay đổi."
            )
        if clip.locked:
            raise ValueError(
                "AI đề xuất sửa cảnh đã khóa. Hãy tạo lại đề xuất hoặc mở khóa cảnh trước."
            )
        if change.asset_id is not None:
            asset = assets.get(change.asset_id)
            if (
                asset is None
                or asset.deleted
                or asset.role != "source"
                or asset.media not in ("video", "image")
            ):
                raise ValueError(
                    f"Đề xuất AI chọn tư liệu không hợp lệ cho cảnh {change.id}."
                )
            start = (
                change.source_start
                if change.source_start is not None
                else clip.source_start
            )
            if asset.media == "video" and start >= asset.duration:
                raise ValueError(
                    f"Mốc nguồn AI đề xuất vượt thời lượng tư liệu cho cảnh {change.id}."
                )
    return {**proposal.model_dump(exclude_none=True), "provider": route}
