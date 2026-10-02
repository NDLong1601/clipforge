"""Add a portable pink-heart review frame inspired by the supplied reference.

Draws new artwork from geometry. Editable text remains separate from the frame.
Build and verification take place outside the user's active project.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

import av
import httpx
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import media, render, store, template_packages
from backend.models import CaptionStyle, Clip, Layer, Project, Template, Viewport, uid

PID = "1fb1c7182463456e"
NAME = "Review sản phẩm · Thẻ hồng · Trái tim"
REVIEW = ROOT / "review" / "pink-heart-template"


def artwork(path):
    scale = 2
    w, h = 1080, 960
    image = Image.new("RGBA", (w * scale, h * scale))
    draw = ImageDraw.Draw(image)

    def box(values):
        return tuple(round(value * scale) for value in values)

    # Fade the source into the pink lower background instead of exposing a
    # straight horizontal edge at the viewport boundary.
    for y in range(h):
        opacity = round(255 * min(1, max(0, (y - 45) / 320)) ** 1.3)
        t = y / h
        draw.line(
            box((0, y, w, y)),
            fill=(255, round(211 - 10 * t), round(220 - 8 * t), opacity),
            width=scale,
        )
    glow = Image.new("RGBA", image.size)
    gd = ImageDraw.Draw(glow)
    gd.ellipse(box((200, 490, 930, 990)), fill=(255, 246, 232, 70))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(75 * scale)))

    shadow = Image.new("RGBA", image.size)
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        box((105, 340, 990, 743)), radius=62 * scale, fill=(160, 68, 94, 55)
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14 * scale)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        box((98, 312, 982, 734)),
        radius=62 * scale,
        fill="#fff4e6",
        outline="#f1b3b9",
        width=3 * scale,
    )
    draw.rounded_rectangle(
        box((122, 335, 958, 710)),
        radius=50 * scale,
        fill="#f8bdbd",
        outline="#ffe3d7",
        width=4 * scale,
    )

    # Raised rose card overlaps the cream backing. Its interior is opaque so
    # source subtitles below it cannot show through the product description.
    card_shadow = Image.new("RGBA", image.size)
    cd = ImageDraw.Draw(card_shadow)
    cd.rounded_rectangle(
        box((197, 243, 902, 647)), radius=53 * scale, fill=(137, 51, 76, 65)
    )
    image.alpha_composite(card_shadow.filter(ImageFilter.GaussianBlur(9 * scale)))
    mask = Image.new("L", image.size)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle(box((190, 225, 896, 635)), radius=51 * scale, fill=255)
    card = Image.new("RGBA", image.size)
    cd = ImageDraw.Draw(card)
    for y in range(225, 636):
        t = (y - 225) / 410
        cd.line(
            box((190, y, 896, y)),
            fill=(round(246 - 9 * t), round(151 - 9 * t), round(162 - 9 * t), 255),
            width=scale,
        )
    card.putalpha(mask)
    image.alpha_composite(card)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        box((190, 225, 896, 635)), radius=51 * scale, outline="#ee808f", width=5 * scale
    )
    draw.rounded_rectangle(
        box((202, 237, 884, 623)), radius=42 * scale, outline="#fbb4b7", width=3 * scale
    )

    # Heart badge is constructed from a parametric curve, not a captured logo.
    badge_shadow = Image.new("RGBA", image.size)
    bd = ImageDraw.Draw(badge_shadow)
    bd.ellipse(box((117, 173, 301, 359)), fill=(173, 77, 104, 65))
    image.alpha_composite(badge_shadow.filter(ImageFilter.GaussianBlur(6 * scale)))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        box((113, 160, 297, 344)), fill="#fff8ec", outline="#ffdde0", width=5 * scale
    )
    draw.ellipse(box((125, 172, 285, 332)), outline="#f8e8d7", width=3 * scale)
    points = []
    for index in range(241):
        t = index / 240 * math.tau
        x = 16 * math.sin(t) ** 3
        y = (
            13 * math.cos(t)
            - 5 * math.cos(2 * t)
            - 2 * math.cos(3 * t)
            - math.cos(4 * t)
        )
        points.append(box((205 + 3.1 * x, 249 - 3.1 * y)))
    draw.polygon(points, fill="#ed829f")
    draw.line(points, fill="#e97696", width=3 * scale)

    def sparkle(x, y, size):
        draw.polygon(
            [
                box(p)
                for p in [
                    (x, y - size),
                    (x + size * 0.24, y - size * 0.24),
                    (x + size, y),
                    (x + size * 0.24, y + size * 0.24),
                    (x, y + size),
                    (x - size * 0.24, y + size * 0.24),
                    (x - size, y),
                    (x - size * 0.24, y - size * 0.24),
                ]
            ],
            fill="#fff8e9",
        )

    for x, y, size in [
        (915, 278, 15),
        (943, 304, 9),
        (169, 611, 16),
        (183, 638, 7),
        (77, 665, 9),
        (1000, 782, 8),
        (480, 792, 6),
        (91, 234, 7),
    ]:
        sparkle(x, y, size)
    # Sparse dots, like soft stationery glitter, remain away from editable text.
    for index in range(48):
        x, y = (67 + index * 137) % 1070, 210 + (index * 89) % 630
        if 180 < x < 910 and y < 650:
            continue
        radius = 1.5 + (index % 3) * 0.8
        draw.ellipse(
            box((x - radius, y - radius, x + radius, y + radius)),
            fill=(255, 247, 232, 180),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((w, h), Image.Resampling.LANCZOS).save(path)


def template(asset_id):
    return Template(
        name=NAME,
        background="#facbd0",
        viewport=Viewport(x=0, y=0, w=1, h=0.72),
        layers=[
            Layer(
                kind="image",
                x=0,
                y=0.5,
                w=1,
                h=0.5,
                asset_id=asset_id,
                text="Nền hồng pastel / thẻ trái tim",
            ),
            Layer(
                kind="text",
                x=0.273,
                y=0.645,
                w=0.5,
                h=0.072,
                size=51,
                text="TÊN SẢN PHẨM",
                color="#68273f",
                background="#f59aa5",
            ),
            Layer(
                kind="text",
                x=0.273,
                y=0.722,
                w=0.49,
                h=0.095,
                size=34,
                text="MÔ TẢ SẢN PHẨM",
                color="#5c293e",
                background="#f59aa5",
            ),
        ],
        caption=CaptionStyle(enabled=False, font_size=48, bottom=0.49),
        slot_durations=[1.0] * 18,
        transition="cut",
        transition_duration=0.35,
        notes="Mẫu review dọc 9:16 theo khung hồng pastel: video phía trên, nền hòa mờ, "
        "thẻ hồng/kem bo góc, huy hiệu trái tim và điểm sáng nhẹ. Tiêu đề màu đỏ rượu "
        "tương phản; sửa tên sản phẩm (1–2 dòng) và mô tả (3–4 dòng) ở Các lớp trong mẫu. "
        "Nhịp mẫu 18 ô ngắn, cắt thẳng; khi tự dựng các ô co theo audio. Tắt Dựng mượt "
        "để giữ kiểu cắt này. Gom góc cùng hành động thành cụm 2–3 giây, khoảng 1 giây/góc. "
        "Đưa chữ nguồn sau thẻ hoặc chỉnh vùng làm mờ theo tư liệu mới. Phụ đề tắt sẵn.",
    )


class Job:
    id = "pink-heart-sample"
    progress = 0

    def update(self, value, message):
        self.progress = value

    def check(self):
        pass


def main():
    REVIEW.mkdir(parents=True, exist_ok=True)
    art = REVIEW / "pink-heart-art.png"
    artwork(art)
    actual_root = store.ROOT
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=60) as client:
        response = client.get(f"/api/projects/{PID}")
        response.raise_for_status()
        active = response.json()
        packages_before = client.get("/api/template-packages").json()
        existing = next(
            (item for item in packages_before if item["name"] == NAME), None
        )
        with tempfile.TemporaryDirectory(
            prefix="clipforge-pink-template-"
        ) as temporary:
            try:
                store.ROOT = Path(temporary)
                p = store.save(
                    Project(
                        name="Mẫu thẻ hồng",
                        aspect="9:16",
                        target_duration=5,
                        smooth_transitions=False,
                    )
                )
                destination = store.project_dir(p.id) / "assets" / "pink-heart.png"
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(art, destination)
                asset = media.register(
                    p.id, destination, "Khung thẻ hồng trái tim", "overlay"
                )
                p.assets.append(asset)
                p.template = template(asset.id)
                candidate = p.template.model_dump()
                response = client.post("/api/templates/validate", json=candidate)
                response.raise_for_status()
                p.template = Template.model_validate(response.json())
                saved = template_packages.save_package(p, p.template)
                generated = store.ROOT / "templates" / saved["id"]
                package_id = existing["id"] if existing else saved["id"]
                package_template = Template.model_validate_json(
                    (generated / "template.json").read_text(encoding="utf-8")
                )
                package_template.id = package_id
                manifest = json.loads(
                    (generated / "manifest.json").read_text(encoding="utf-8")
                )
                manifest.update(id=package_id, name=NAME)
                store.atomic(generated / "template.json", package_template.model_dump())
                store.atomic(generated / "manifest.json", manifest)
                # Apply the finished package to a separate project. Only its
                # bundled artwork supplies the image; the user project is untouched.
                if package_id != saved["id"]:
                    moved = store.ROOT / "templates" / package_id
                    os.rename(generated, moved)
                    generated = moved
                sample = store.save(
                    Project(
                        name="Xem khung thẻ hồng",
                        aspect="9:16",
                        target_duration=5,
                        smooth_transitions=False,
                    )
                )
                sample = template_packages.apply_package(sample, package_id)
                assert sample.mode == "template"
                source_mapping = {}
                for index in [0, 1, 4, 9, 12]:
                    original = active["clips"][index]
                    original_asset = next(
                        a for a in active["assets"] if a["id"] == original["asset_id"]
                    )
                    if original_asset["id"] not in source_mapping:
                        source = (
                            actual_root
                            / "projects"
                            / PID
                            / "assets"
                            / original_asset["filename"]
                        )
                        target = (
                            store.project_dir(sample.id)
                            / "assets"
                            / original_asset["filename"]
                        )
                        shutil.copyfile(source, target)
                        new_asset = media.register(
                            sample.id, target, original_asset["name"], "source"
                        )
                        # Retain scene coordinates and text detection for this QA sample.
                        from backend.models import Scene

                        new_asset.scenes = [
                            Scene.model_validate({**scene, "thumbnail": ""})
                            for scene in original_asset["scenes"]
                        ]
                        sample.assets.append(new_asset)
                        source_mapping[original_asset["id"]] = new_asset.id
                    clip = Clip.model_validate(original)
                    clip.id = uid()
                    clip.asset_id = source_mapping[original_asset["id"]]
                    clip.duration = 1
                    # This source's subtitle was hidden by the old yellow card.
                    # Reframe it for the lower top edge of the pink card.
                    if (
                        original_asset["name"] == "Video 3.mp4"
                        and clip.source_start == 16
                    ):
                        clip.crop_y = 0
                    sample.clips.append(clip)
                sample.source_text_mode = "blur"
                sample.template.layers[1].text = (
                    "TẠ ĐEO TAY KHÁNG LỰC\nVẬN ĐỘNG MỖI NGÀY"
                )
                sample.template.layers[2].text = (
                    "Đeo khi làm việc nhà,\nđi dạo hoặc vận động nhẹ.\n"
                    "Tiện lợi cho người bận rộn."
                )
                sample = store.save(sample)
                rendered = render.render(sample, Job(), preview=True)
                sample_video = (
                    store.project_dir(sample.id)
                    / "exports"
                    / Job.id
                    / rendered["filename"]
                )
                shutil.copyfile(sample_video, REVIEW / "sample-preview.mp4")
                with av.open(str(sample_video)) as container:
                    container.seek(400000)
                    frame = next(
                        frame
                        for frame in container.decode(video=0)
                        if frame.time >= 0.4
                    )
                    frame.to_image().save(REVIEW / "sample-frame.png")
                bundled = template_packages._load_package(package_id)
                assert all(record["_path"] for record in bundled[1].values())
                final = actual_root / "templates" / package_id
                if existing:
                    # Keep a prior package intact, and publish the refreshed
                    # generated asset filenames before its atomic metadata.
                    backup = REVIEW / "package-before"
                    if not backup.exists():
                        shutil.copytree(final, backup)
                    for record in manifest["resources"].values():
                        target = final / record["filename"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(generated / record["filename"], target)
                    store.atomic(final / "template.json", package_template.model_dump())
                    store.atomic(final / "manifest.json", manifest)
                else:
                    staging = actual_root / "templates" / f".pink-heart-{package_id}"
                    shutil.copytree(generated, staging)
                    os.rename(staging, final)
                (REVIEW / "template.json").write_text(
                    json.dumps(
                        package_template.model_dump(), ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
            finally:
                store.ROOT = actual_root
        response = client.get("/api/template-packages")
        response.raise_for_status()
        packages_after = response.json()
        saved = next(item for item in packages_after if item["id"] == package_id)
        assert saved["name"] == NAME and not saved["missing_assets"]
        old_id = "517e15cd0d2e486c"
        assert next(item for item in packages_after if item["id"] == old_id) == next(
            item for item in packages_before if item["id"] == old_id
        )
        assert client.get(f"/api/projects/{PID}").json() == active
        response = client.get(
            f"/api/templates/{package_id}/thumbnail.svg", params={"aspect": "9:16"}
        )
        response.raise_for_status()
        assert "data:image/png;base64," in response.text
        (REVIEW / "thumbnail.svg").write_text(response.text, encoding="utf-8")
    result = {
        "package_id": package_id,
        "name": NAME,
        "package_path": str(final),
        "missing_assets": [],
        "editable_text_layers": 2,
        "caption_enabled": False,
        "slots": 18,
        "transition": "cut",
        "old_template_unchanged": True,
        "active_project_unchanged": True,
        "cross_project_apply_verified": True,
        "rendered_sample": media.probe(REVIEW / "sample-preview.mp4"),
    }
    (REVIEW / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
