"""Re-edit the current product video into short, related action sequences.

Uses the running API so revision checks, history and render jobs remain active.
The reference supplies pace only; the user's narration and source footage stay
in the product project. Scene choices were checked against decoded source frames.
"""

import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import store
from backend.models import Clip, Template, TextRegion
from backend.timeline_math import timeline_duration

PID = "1fb1c7182463456e"
REVIEW = ROOT / "review" / "fast-rhythm"

# Each 2–3 second action sequence contains several different shots, rather than
# extending a single source shot or jumping between unrelated actions each beat.
SHOTS = [
    ("Cận cảnh sản phẩm", "Video 1.mp4", 13.2, 0.5, 0.25),
    ("Cận cảnh sản phẩm", "Video 2.mp4", 28.0, 0.5, 0.5),
    ("Cận cảnh sản phẩm", "Video 1.mp4", 7.6, 0.5, 0.25),
    ("Đeo tạ / vận động nhẹ", "Video 1.mp4", 10.8, 0.5, 0.25),
    ("Đeo tạ / vận động nhẹ", "Video 3.mp4", 16.0, 0.5, 0.25),
    ("Đeo tạ / vận động nhẹ", "Video 3.mp4", 24.0, 0.5, 0.15),
    ("Làm việc nhà", "Video 1.mp4", 26.4, 0.5, 0.25),
    ("Làm việc nhà", "Video 2.mp4", 24.4, 0.35, 0.5),
    ("Làm việc nhà", "Video 3.mp4", 13.2, 0.5, 0.25),
    ("Đi lại", "Video 1.mp4", 16.8, 0.5, 0.4),
    ("Đi lại", "Video 3.mp4", 4.8, 0.5, 0.25),
    ("Đi lại", "Video 1.mp4", 24.8, 0.5, 0.35),
    ("Dùng điện thoại", "Video 2.mp4", 9.6, 0.6, 0.5),
    ("Dùng điện thoại", "Video 1.mp4", 20.8, 0.5, 0.25),
    ("Vận động nhẹ", "Video 1.mp4", 15.6, 0.5, 0.25),
    ("Vận động nhẹ", "Video 1.mp4", 18.8, 0.5, 0.25),
    ("Cận cảnh sản phẩm", "Video 1.mp4", 29.2, 0.5, 0.25),
    ("Cận cảnh sản phẩm", "Video 3.mp4", 18.8, 0.5, 0.15),
]


def contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in [1, 3, 5]]
        linear = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return sum(c * weight for c, weight in zip(linear, [0.2126, 0.7152, 0.0722]))

    a, b = sorted([luminance(foreground), luminance(background)])
    return (b + 0.05) / (a + 0.05)


def clean_source_text(clip, name, start):
    # Wide sources have subtitles across their bottom edge. Include the whole
    # strip so letters cannot peek out beside the opaque product card.
    if name == "Video 2.mp4":
        clip.text_mode = "blur"
        clip.text_regions_override = True
        clip.text_regions = [TextRegion(x=0, y=0.8, w=1, h=0.2)]
    # These reframed portrait shots put their subtitles behind the card; avoid
    # a visible blur rectangle on the ankle weight or walking feet.
    elif (name, start) in [("Video 3.mp4", 16.0), ("Video 1.mp4", 16.8)]:
        clip.text_mode = "off"
    return Clip.model_validate(clip.model_dump())


def main():
    REVIEW.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=60) as client:

        def request(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        url = f"/api/projects/{PID}"
        p = request("GET", url)
        (REVIEW / "project-before.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if any(clip["locked"] for clip in p["clips"]):
            raise ValueError(
                "Có cảnh đã khóa; cần giữ vị trí các cảnh đó trước khi thay nhịp."
            )
        voice = next(asset for asset in p["assets"] if asset["id"] == p["voice_id"])
        duration = float(voice["duration"])
        if not 17 < duration < 19:
            raise ValueError(
                "Danh sách cảnh đã chuẩn bị cho narration 18 giây hiện tại."
            )
        clips = []
        rows = []
        position = 0.0
        for index, (action, name, start, crop_x, crop_y) in enumerate(SHOTS):
            asset = next(
                asset
                for asset in p["assets"]
                if asset["name"] == name and not asset["deleted"]
            )
            scene = next(
                scene
                for scene in asset["scenes"]
                if abs(scene["start"] - start) < 0.001
            )
            length = 1.0 if index < len(SHOTS) - 1 else round(duration - position, 6)
            available = scene["end"] - start - 0.02
            speed = min(1.0, available / length)
            if not 0.7 <= speed <= 1:
                raise ValueError(f"Nguồn {name} tại {start}s không đủ cho nhịp này.")
            clip = Clip(
                asset_id=asset["id"],
                scene_id=scene["id"],
                source_start=start,
                duration=length,
                speed=round(speed, 6),
                fit="cover",
                crop_x=crop_x,
                crop_y=crop_y,
                transition="cut",
                title=action,
                text_mode="inherit",
            )
            clip = clean_source_text(clip, name, start)
            clips.append(clip)
            rows.append(
                {
                    "start": round(position, 6),
                    "duration": length,
                    "action": action,
                    "source": name,
                    "source_start": start,
                    "speed": clip.speed,
                }
            )
            assert start + length * clip.speed <= scene["end"] + 0.000001
            position += length
        assert abs(timeline_duration(clips) - duration) < 0.00001
        assert max(clip.duration for clip in clips) <= 3

        headline = next(
            layer
            for layer in p["template"]["layers"]
            if layer["kind"] == "text" and "TẠ ĐEO TAY" in layer["text"]
        )
        headline.update(color="#14334a", background="#fff1c7")
        p["clips"] = [clip.model_dump() for clip in clips]
        p["template"]["slot_durations"] = [clip.duration for clip in clips]
        p["template"]["transition"] = "cut"
        p["template"]["notes"] = (
            "Review sản phẩm · thẻ vàng, tiêu đề xanh than tương phản. "
            "Nhịp bản audio hiện tại: 18 góc, khoảng 1 giây/góc, cắt thẳng. "
            "Gom các góc cùng hành động trong cụm 2–3 giây: sản phẩm, đeo tạ, việc nhà, "
            "đi lại, điện thoại, vận động nhẹ. Tiêu đề, mô tả và CTA chỉnh ở Các lớp trong mẫu."
        )
        p["smooth_transitions"] = False
        p["target_duration"] = duration
        p = request("PUT", url, json=p)
        report = request("GET", url + "/preflight")
        if not report["ok"]:
            raise ValueError(report)
        package_id = p["template"]["id"]
        package_path = store.ROOT / "templates" / package_id / "template.json"
        if package_path.is_file():
            store.atomic(
                package_path, Template.model_validate(p["template"]).model_dump()
            )
        job = request("POST", url + "/render", json={"preview": True})
        result = {
            "project_id": PID,
            "revision": p["revision"],
            "package_id": package_id,
            "preview_job": job["id"],
            "duration": duration,
            "clips": len(clips),
            "preflight": report,
            "headline_color": headline["color"],
            "headline_contrast": round(contrast(headline["color"], "#fff1c7"), 2),
            "caption_enabled": p["template"]["caption"]["enabled"],
            "timeline": rows,
        }
        (REVIEW / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {key: value for key, value in result.items() if key != "timeline"},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
