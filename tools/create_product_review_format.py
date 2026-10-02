"""Create original vector-style artwork and an editable template for a review video.

The supplied reference informs layout only. No frame, watermark or brand from
the reference is copied into the artwork. Run with the project's Python runtime.
"""

from pathlib import Path
import json
import sys

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.models import CaptionStyle, Layer, Template, Viewport


def artwork(path: Path):
    # A 1080 x 960 transparent lower-half overlay. Supersampling preserves the
    # curved outlines, small dot pattern and cart strokes at preview resolution.
    scale = 2
    w, h = 1080, 960
    image = Image.new("RGBA", (w * scale, h * scale))

    def points(values):
        return tuple(round(value * scale) for value in values)

    draw = ImageDraw.Draw(image)
    for y in range(280, h):
        t = (y - 280) / (h - 280)
        color = (255, round(213 + t * 20), round(16 + t * 17), 255)
        draw.line(points((0, y, w, y)), fill=color, width=scale)
    # Broad, quiet diagonal facets and a light halftone texture, as in a shop
    # review banner. They sit below the card instead of competing with the video.
    draw.polygon(
        [points(p) for p in [(0, 740), (500, 370), (820, 370), (0, 960)]],
        fill="#ffe863",
    )
    draw.polygon(
        [
            points(p)
            for p in [(280, 960), (920, 520), (1080, 520), (1080, 680), (690, 960)]
        ],
        fill="#ffdc27",
    )
    for y in range(600, 955, 25):
        for x in range(685, 1080, 25):
            radius = 2.1 + 1.1 * (x - 685) / 395
            draw.ellipse(
                points((x - radius, y - radius, x + radius, y + radius)), fill="#fff39c"
            )

    shadow = Image.new("RGBA", image.size)
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        points((119, 134, 1005, 632)), radius=27 * scale, fill=(72, 48, 0, 62)
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(17 * scale)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        points((102, 78, 992, 590)),
        radius=32 * scale,
        outline="#fff8dc",
        width=5 * scale,
    )
    draw.rounded_rectangle(
        points((127, 107, 972, 610)),
        radius=27 * scale,
        fill="#fff1c7",
        outline="#e5ba40",
        width=3 * scale,
    )
    draw.rounded_rectangle(
        points((144, 124, 955, 593)),
        radius=19 * scale,
        outline="#fffaf0",
        width=2 * scale,
    )

    # Shopping cart medallion, drawn from geometry rather than a copied logo.
    badge_shadow = Image.new("RGBA", image.size)
    bd = ImageDraw.Draw(badge_shadow)
    bd.ellipse(points((43, 300, 333, 590)), fill=(72, 45, 0, 88))
    image.alpha_composite(badge_shadow.filter(ImageFilter.GaussianBlur(8 * scale)))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        points((33, 282, 323, 572)), fill="#f0a100", outline="#8c5c00", width=3 * scale
    )
    draw.ellipse(
        points((44, 291, 312, 559)), fill="#ffc622", outline="#ffe78a", width=5 * scale
    )
    draw.arc(points((59, 303, 298, 542)), 203, 309, fill="#fff3b8", width=5 * scale)
    cart = [(87, 361), (112, 361), (139, 461), (245, 461)]
    draw.line(
        [points(p) for p in cart], fill="#fff5cf", width=16 * scale, joint="curve"
    )
    draw.line([points(p) for p in cart], fill="#67430c", width=8 * scale, joint="curve")
    draw.polygon(
        [points(p) for p in [(119, 376), (270, 376), (251, 430), (135, 430)]],
        fill="#67430c",
    )
    for y in [391, 407]:
        draw.line(points((133, y, 252, y)), fill="#ffd24d", width=4 * scale)
    for x in [152, 180, 208, 236]:
        draw.line(points((x, 382, x - 5, 425)), fill="#ffd24d", width=3 * scale)
    for x in [153, 232]:
        draw.ellipse(points((x - 12, 478, x + 12, 502)), fill="#fff5cf")
        draw.ellipse(points((x - 8, 482, x + 8, 498)), fill="#67430c")

    # A CTA pill with editable text provided by a separate template layer.
    draw.rounded_rectangle(
        points((183, 746, 897, 843)), radius=48 * scale, fill="#573a0d"
    )
    draw.line(
        [points(p) for p in [(843, 782), (855, 794), (843, 806)]],
        fill="#ffe367",
        width=5 * scale,
        joint="curve",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((w, h), Image.Resampling.LANCZOS).save(path)


def template(art_asset_id: str):
    return Template(
        name="Review sản phẩm · Thẻ vàng",
        background="#ffde26",
        viewport=Viewport(x=0, y=0, w=1, h=0.68),
        layers=[
            Layer(
                kind="image",
                x=0,
                y=0.5,
                w=1,
                h=0.5,
                asset_id=art_asset_id,
                text="Nền thẻ vàng / giỏ hàng",
            ),
            Layer(
                kind="text",
                x=0.237,
                y=0.574,
                w=0.63,
                h=0.092,
                text="TẠ ĐEO TAY KHÁNG LỰC\nVẬN ĐỘNG MỖI NGÀY",
                size=54,
                color="#14334a",
                background="#fff1c7",
            ),
            Layer(
                kind="text",
                x=0.335,
                y=0.682,
                w=0.53,
                h=0.109,
                text="Đeo khi làm việc nhà,\nđi dạo hoặc vận động nhẹ.\nTiện lợi cho người bận rộn.",
                size=36,
                color="#4b3510",
                background="#fff1c7",
            ),
            Layer(
                kind="text",
                x=0.227,
                y=0.897,
                w=0.52,
                h=0.033,
                text="KHÁM PHÁ SẢN PHẨM",
                size=37,
                color="#ffe570",
                background="#573a0d",
            ),
        ],
        slot_durations=[1.0] * 18,
        caption=CaptionStyle(
            enabled=False,
            font_size=48,
            bottom=0.485,
            words_per_line=7,
            color="#ffffff",
            karaoke=False,
        ),
        transition="cut",
        transition_duration=0.35,
        notes="Format 9:16 theo video tham chiếu: hình sản phẩm phía trên, thẻ vàng bo góc và "
        "biểu tượng giỏ hàng phía dưới. Nhịp mẫu 18 ô ngắn, cắt thẳng. Chỉnh tiêu đề, mô tả và CTA "
        "trong Các lớp trong mẫu. Cắt khung để tránh chữ gốc; dùng làm mờ cho vùng còn lại.",
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "review" / "reference-format"
    artwork(root / "product-review-art.png")
    (root / "template-draft.json").write_text(
        json.dumps(template("").model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(root / "product-review-art.png")
