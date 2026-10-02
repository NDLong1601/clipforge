import math, re, unicodedata, json
from collections import Counter
from .models import Template, Layer, Viewport, Clip, Cue
from .providers import sentences, ai_json
from .timeline_math import clip_starts, overlap_before, timeline_duration
from .store import get_asset


def presets():
    return [
        Template(
            id="clean",
            name="Toàn khung",
            layers=[
                Layer(
                    kind="text",
                    text="{project}",
                    x=0.07,
                    y=0.06,
                    w=0.86,
                    h=0.07,
                    size=30,
                    color="#b5f36d",
                )
            ],
        ),
        Template(
            id="editorial",
            name="Khung biên tập",
            background="#f0eadf",
            viewport=Viewport(x=0.055, y=0.2, w=0.89, h=0.57),
            layers=[
                Layer(
                    kind="text",
                    text="{title}",
                    x=0.06,
                    y=0.055,
                    w=0.88,
                    h=0.13,
                    size=60,
                    color="#20221f",
                ),
                Layer(
                    kind="rect",
                    x=0.06,
                    y=0.79,
                    w=0.18,
                    h=0.009,
                    color="#2b703e",
                    text="",
                ),
                Layer(
                    kind="text",
                    text="{project}",
                    x=0.06,
                    y=0.81,
                    w=0.88,
                    h=0.06,
                    size=27,
                    color="#2b703e",
                ),
            ],
            caption={"color": "#20221f", "highlight": "#2b703e", "bottom": 0.06},
        ),
        Template(
            id="bold",
            name="Nhịp nhanh",
            background="#141414",
            viewport=Viewport(x=0, y=0.13, w=1, h=0.72),
            layers=[
                Layer(kind="rect", x=0, y=0, w=1, h=0.13, color="#b5f36d", text=""),
                Layer(
                    kind="text",
                    x=0.06,
                    y=0.025,
                    w=0.88,
                    h=0.095,
                    text="{title}",
                    size=58,
                    color="#151515",
                    animation="slide",
                ),
            ],
            transition="fade",
            caption={"bottom": 0.06},
        ),
    ]


def tokens(text):
    s = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return set(re.findall(r"\w+", s))


def estimated_cues(script, duration):
    parts = sentences(script)
    if not parts:
        return []
    weights = [max(1, len(s.split())) for s in parts]
    total = sum(weights)
    pos = 0
    out = []
    for text, weight in zip(parts, weights):
        end = pos + duration * weight / total
        out.append(Cue(start=round(pos, 3), end=round(end, 3), text=text))
        pos = end
    return out


def timed_words(cues):
    words = []
    for cue in cues:
        parts = cue.text.split()
        weights = [max(1, len(w)) for w in parts]
        total = sum(weights)
        pos = cue.start
        for word, weight in zip(parts, weights):
            end = pos + (cue.end - cue.start) * weight / total
            words.append(Cue(start=pos, end=end, text=word))
            pos = end
    return words


def caption_groups(cues, max_words=6):
    words = timed_words(cues)
    groups = []
    current = []
    for word in words:
        if current and (
            len(current) >= max_words
            or word.start - current[-1].end > 0.45
            or re.search(r"[.!?。]$", current[-1].text)
        ):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def speech_boundaries(cues, duration):
    """Prefer sentence ends and pauses, then word ends for long spoken ideas."""
    words = timed_words(cues)
    preferred = [
        word.end
        for index, word in enumerate(words)
        if re.search(r"[.!?。]$", word.text)
        or (index + 1 < len(words) and words[index + 1].start - word.end > 0.3)
    ]
    # Gemini's segments are already short spoken ideas.
    preferred.extend(cue.end for cue in cues if len(cue.text.split()) > 1)
    count = max(1, round(duration / 2.8))
    boundaries = [0.0]
    for index in range(1, count):
        ideal = duration * index / count
        candidates = [
            value
            for value in preferred
            if abs(value - ideal) < 0.9
            and value - boundaries[-1] >= 1.2
            and duration - value >= (count - index) * 1.2
        ]
        if not candidates:
            candidates = [
                word.end
                for word in words
                if abs(word.end - ideal) < 0.3
                and word.end - boundaries[-1] >= 1.2
                and duration - word.end >= (count - index) * 1.2
            ]
        boundaries.append(
            min(candidates, key=lambda value: abs(value - ideal))
            if candidates
            else ideal
        )
    return boundaries + [duration]


def plan(p, use_ai, job):
    if use_ai:
        from .template_content import bind_placeholders, infer_product

        bind_placeholders(p.template)
        infer_product(p, job)
    pool = [
        (a, sc)
        for a in p.assets
        if a.role == "source" and not a.deleted
        for sc in a.scenes
    ]
    if p.avoid_faces:
        from .face_filter import clean_pool, check_locked

        check_locked(p, job)
        pool = clean_pool(p, job)
        if not pool:
            raise ValueError(
                "Chưa có cảnh không có mặt được kiểm tra. Thêm ảnh sản phẩm hoặc video chỉ quay tay/sản phẩm."
            )
    if not pool:
        raise ValueError("Thêm tư liệu và bấm Phân tích cảnh trước khi lập timeline")
    duration = p.target_duration
    if p.voice_id:
        voice = get_asset(p, p.voice_id)
        duration = max(duration, voice.duration)
    if duration > 180:
        raise ValueError("Video đầu ra tối đa 180 giây")
    if not p.cues or (
        p.cue_timing == "estimated" and not p.cues_edited and not p.cues_stale
    ):
        p.cues = estimated_cues(p.script, duration)
        p.cue_timing = "estimated"
        p.cues_edited = False
        p.cues_stale = False
    locked_positions = []
    old_starts = clip_starts(p.clips)
    for index, clip in enumerate(p.clips):
        if clip.locked:
            locked_positions.append((old_starts[index], clip.model_copy(deep=True)))

    generated = []
    timeline = []
    if locked_positions:
        last = 0.0

        def fill_gap(start, end):
            length = end - start
            if length < 0.001:
                return
            if length < 0.2 - 0.000001:
                raise ValueError(
                    f"Khoảng trống {length:.3f}s quanh cảnh đã khóa nhỏ hơn cảnh tối thiểu 0.2s. Hãy chỉnh thời lượng hoặc vị trí các cảnh khóa."
                )
            if p.mode == "template" and p.template.slot_durations:
                slots = [max(0.2, float(value)) for value in p.template.slot_durations]
                count = max(1, round(length / (sum(slots) / len(slots))))
                count = min(count, max(1, int((length + 1e-8) / 0.2)))
                weights = [slots[index % len(slots)] for index in range(count)]
                durations = [length * value / sum(weights) for value in weights]
                if any(value < 0.2 - 0.000001 for value in durations):
                    raise ValueError(
                        "Mẫu không thể lấp khoảng trống quanh cảnh khóa mà vẫn giữ ô tối thiểu 0.2s."
                    )
                boundaries = [start]
                for value in durations[:-1]:
                    boundaries.append(boundaries[-1] + value)
                boundaries.append(end)
            else:
                count = max(1, round(length / 2.5))
                count = min(count, max(1, int((length + 1e-8) / 0.2)))
                boundaries = [start]
                word_ends = [word.end for word in timed_words(p.cues)]
                for index in range(1, count):
                    ideal = start + length * index / count
                    remaining = count - index
                    candidates = [
                        value
                        for value in word_ends
                        if abs(value - ideal) < 0.25
                        and value - boundaries[-1] >= 0.2
                        and end - value >= 0.2 * remaining
                    ]
                    boundaries.append(
                        min(candidates, key=lambda value: abs(value - ideal))
                        if candidates
                        else ideal
                    )
                boundaries.append(end)
            for left, right in zip(boundaries, boundaries[1:]):
                if right - left < 0.2 - 0.000001:
                    raise ValueError(
                        "Không thể lấp khoảng trống giữa các cảnh khóa mà vẫn giữ cảnh tối thiểu 0.2s."
                    )
                item = {
                    "start": left,
                    "duration": right - left,
                    "text": " ".join(
                        c.text for c in p.cues if c.start < right and c.end > left
                    ),
                }
                generated.append(item)
                timeline.append(item)

        for locked_start, clip in locked_positions:
            locked_end = locked_start + clip.duration
            if locked_end > duration + 0.000001:
                raise ValueError(
                    f"Cảnh đã khóa kết thúc ở {locked_end:.2f}s, vượt thời lượng đích {duration:.2f}s. Hãy tăng thời lượng hoặc mở khóa cảnh."
                )
            incoming_overlap = (
                clip.transition_duration
                if clip.transition == "crossfade"
                and (timeline or locked_start > 0.000001)
                else 0
            )
            previous_end = locked_start + incoming_overlap
            if previous_end < last - 0.000001:
                raise ValueError(
                    "Vị trí các cảnh đã khóa bị chồng lấn; hãy chỉnh timeline trước khi dựng lại."
                )
            fill_gap(last, previous_end)
            marker = {"locked_clip": clip, "start": locked_start}
            timeline.append(marker)
            last = locked_end
        fill_gap(last, duration)
        if len(timeline) > 150:
            raise ValueError(
                "Timeline có hơn 150 cảnh sau khi giữ các cảnh khóa. Mở khóa hoặc gộp một số cảnh."
            )
    elif p.mode == "template" and p.template.slot_durations:
        ds = [max(0.2, float(d)) for d in p.template.slot_durations]
        durations = [d * duration / sum(ds) for d in ds]
        if any(d < 0.2 for d in durations):
            raise ValueError(
                "Mẫu có ô quá ngắn sau khi co thời lượng. Giảm số ô hoặc tăng thời lượng video."
            )
    else:
        # Snap cuts near speech word boundaries, keeping a preferred 2–3 second rhythm.
        n = max(1, round(duration / 2.5))
        boundaries = [0.0]
        ends = [w.end for w in timed_words(p.cues)]
        for i in range(1, n):
            ideal = duration * i / n
            candidates = [
                t for t in ends if abs(t - ideal) < 0.25 and t - boundaries[-1] >= 1.8
            ]
            boundaries.append(
                min(candidates, key=lambda t: abs(t - ideal)) if candidates else ideal
            )
        boundaries.append(duration)
        if p.smooth_transitions and p.cue_timing in ("transcribed", "audio_segments"):
            boundaries = speech_boundaries(p.cues, duration)
        durations = [b - a for a, b in zip(boundaries, boundaries[1:])]
    if not locked_positions:
        positions = []
        transition = "crossfade" if p.smooth_transitions else p.template.transition
        overlap = (
            min(0.35, p.template.transition_duration)
            if p.smooth_transitions
            else p.template.transition_duration
        )
        overlap = overlap if transition == "crossfade" else 0
        if p.smooth_transitions:
            capacity = max(
                4 if asset.media == "image" else (scene.end - scene.start - 0.01) / 0.5
                for asset, scene in pool
            )
            durations = [
                value / max(1, math.ceil(value / capacity))
                for value in durations
                for _ in range(max(1, math.ceil(value / capacity)))
            ]
            if len(durations) > 150:
                raise ValueError(
                    "Nguồn quá ngắn để dựng mượt trong giới hạn 150 cảnh. Thêm cảnh dài hơn."
                )
            overlaps = [0] + [
                min(overlap, durations[index - 1] * 0.3, durations[index] * 0.3)
                for index in range(1, len(durations))
            ]
            overlaps = [value if value >= 0.08 else 0 for value in overlaps] + [0]
            durations = [
                value + overlaps[index] / 2 + overlaps[index + 1] / 2
                for index, value in enumerate(durations)
            ]
        elif overlap:
            desired_clip_total = duration + overlap * max(0, len(durations) - 1)
            scale = desired_clip_total / max(sum(durations), 0.000001)
            durations = [value * scale for value in durations]
        position = 0
        for index, d in enumerate(durations):
            before = (
                overlaps[index] if p.smooth_transitions else (overlap if index else 0)
            )
            after = (
                overlaps[index + 1]
                if p.smooth_transitions
                else (overlap if index < len(durations) - 1 else 0)
            )
            positions.append(position)
            visible_start = position + before / 2
            visible_end = position + d - after / 2
            text = " ".join(
                c.text
                for c in p.cues
                if c.start < visible_end and c.end > visible_start
            )
            item = {"start": position, "duration": d, "text": text, "overlap": before}
            generated.append(item)
            timeline.append(item)
            position += d - after
    ai_choices = {}
    if use_ai and generated:
        if any(not scene.ai_labeled for _, scene in pool):
            from .providers import label_scenes

            label_scenes(p, job)
        job.update(20, "AI chọn cảnh theo từng ý của kịch bản")
        catalog = [
            {
                "id": s.id,
                "tags": s.tags,
                "source": a.name,
                "length": round(s.end - s.start, 2),
            }
            for a, s in pool
        ]
        result = ai_json(
            'Chọn cảnh phù hợp từng ý video, tránh lặp và giữ đúng đối tượng/hành động. JSON {"choices":[{"index":0,"scene_id":"..."}]}. Không bịa ID. Các ý: '
            + json.dumps([item["text"] for item in generated], ensure_ascii=False)
            + "\nThư viện: "
            + json.dumps(catalog, ensure_ascii=False),
            job=job,
        )
        ai_choices = {
            x.get("index"): x.get("scene_id")
            for x in result.get("choices", [])
            if isinstance(x, dict)
        }
    used = Counter()
    fingerprints = Counter()
    previous = ""
    clips = []
    generated_index = 0
    for item in timeline:
        job.check()
        if "locked_clip" in item:
            clip = item["locked_clip"]
            clips.append(clip)
            locked_asset = next(
                (asset for asset, _ in pool if asset.id == clip.asset_id), None
            )
            if locked_asset:
                locked_scene = next(
                    (
                        scene
                        for asset, scene in pool
                        if asset.id == clip.asset_id and scene.id == clip.scene_id
                    ),
                    None,
                )
                used[clip.scene_id] += 1
                if locked_scene and locked_scene.fingerprint:
                    fingerprints[locked_scene.fingerprint] += 1
                previous = clip.asset_id
            continue
        d, text = item["duration"], item["text"]
        wanted = tokens(text)

        def score(pair):
            a, s = pair
            return (
                len(wanted & tokens(s.tags + " " + a.tags)) * 3
                + s.quality * 0.2
                + (6 if ai_choices.get(generated_index) == s.id else 0)
                - used[s.id] * 4
                - fingerprints[s.fingerprint] * 1.5
                - (1 if previous == a.id else 0)
                - max(0, d - (s.end - s.start))
            )

        candidates = pool
        if p.smooth_transitions:
            candidates = [
                pair
                for pair in pool
                if pair[0].media == "image"
                or pair[1].end - pair[1].start - 0.01 >= d * 0.25
            ]
            if not candidates:
                raise ValueError(
                    "Không đủ hình nguồn để dựng cảnh mượt. Thêm video dài hơn hoặc giảm thời lượng ô mẫu."
                )
        a, sc = max(candidates, key=score)
        used[sc.id] += 1
        if sc.fingerprint:
            fingerprints[sc.fingerprint] += 1
        previous = a.id
        # Long template slots may extend with a frozen last frame; explicitly flag them.
        transition = (
            (
                "crossfade"
                if p.smooth_transitions and item.get("overlap")
                else p.template.transition
            )
            if not locked_positions
            else "cut"
        )
        if p.smooth_transitions and not item.get("overlap"):
            transition = "cut"
        transition_duration = item.get("overlap") or p.template.transition_duration
        speed = 1
        if p.smooth_transitions and a.media == "video" and sc.end - sc.start < d:
            speed = max(0.25, min(1, (sc.end - sc.start - 0.01) / d))
        clips.append(
            Clip(
                asset_id=a.id,
                scene_id=sc.id,
                source_start=sc.start,
                duration=round(d, 6),
                title=" ".join(text.split()[:9]) or p.name,
                transition=transition,
                speed=round(speed, 6),
                transition_duration=transition_duration,
            )
        )
        generated_index += 1
    if not locked_positions:
        # Correct rounding on the shared project clock, including transition overlap.
        clips[-1].duration = round(
            clips[-1].duration + duration - timeline_duration(clips), 6
        )
    elif abs(timeline_duration(clips) - duration) > 0.00001:
        raise ValueError(
            "Không thể lấp timeline mà vẫn giữ nguyên vị trí các cảnh đã khóa."
        )
    p.clips = clips
    p.warnings = []
    if p.cue_timing == "estimated":
        p.warnings.append(
            "Phụ đề đang ước lượng theo kịch bản. Tạo giọng hoặc căn lại từ file voice để khớp lời đọc."
        )
    if p.cues_stale:
        p.warnings.append(
            "Kịch bản đã thay đổi; phụ đề cũ đang được giữ lại và cần được kiểm tra hoặc tạo lại."
        )
    held = []
    for clip in clips:
        asset = next((asset for asset in p.assets if asset.id == clip.asset_id), None)
        if not asset or asset.media != "video":
            continue
        limit = asset.duration
        if clip.scene_id:
            scene = next(
                (scene for scene in asset.scenes if scene.id == clip.scene_id), None
            )
            if scene:
                limit = min(limit, scene.end)
        held.append(
            max(0, clip.source_start + clip.duration * clip.speed - limit)
            / max(0.01, clip.speed)
        )
    if any(value > 1 / 30 for value in held):
        p.warnings.append(
            "Một số cảnh dài hơn nguồn ở tốc độ đã chọn; renderer sẽ giữ khung hình cuối đúng phần thời lượng thiếu."
        )
    if not use_ai:
        p.warnings.append(
            "Chọn cảnh theo nhãn/từ khóa và độ đa dạng. Bật AI để ghép theo ý nghĩa hình ảnh."
        )
    if p.cue_timing == "audio_segments":
        p.warnings.append(
            "Mốc câu được Gemini ước lượng từ audio; kiểm tra lại nếu cần karaoke chính xác từng từ."
        )
    if p.source_text_mode != "off" and any(
        not scene.text_analyzed for _, scene in pool
    ):
        p.warnings.append(
            "Một số cảnh chưa được nhận diện vùng chữ. Bật AI khi phân tích hoặc thêm vùng che chữ trong điều chỉnh cảnh."
        )
    from .sound_effects import refresh

    return refresh(p)


def validate_timeline(p):
    if not p.clips:
        raise ValueError("Timeline chưa có cảnh")
    duration = timeline_duration(p.clips)
    if duration > 180.1:
        raise ValueError("Tổng timeline vượt 180 giây")
    for clip in p.clips:
        a = get_asset(p, clip.asset_id)
        if a.media not in ["video", "image"]:
            raise ValueError("Timeline chỉ nhận ảnh hoặc video")
        if a.media == "video" and clip.source_start >= a.duration - 0.01:
            raise ValueError("Mốc cắt vượt thời lượng nguồn: " + a.name)
    if p.voice_id and get_asset(p, p.voice_id).duration > duration + 0.15:
        raise ValueError(
            "Timeline ngắn hơn voice. Kéo dài timeline hoặc lập lại để tránh cắt mất lời."
        )
    return duration
