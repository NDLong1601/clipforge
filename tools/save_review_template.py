"""Normalize the approved review format as a reusable, self-contained package.

Refreshes the existing library entry while preserving the current project and
its product-specific text. Requires the project runtime and running local API.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import store, template_packages
from backend.models import Project, Template

PID = "1fb1c7182463456e"
NAME = "Review sản phẩm · Thẻ vàng · Nhịp nhanh"
REVIEW = ROOT / "review" / "reusable-template"


def main():
    REVIEW.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=60) as client:
        response = client.get(f"/api/projects/{PID}")
        response.raise_for_status()
        project_before = response.json()
        candidate = Template.model_validate(project_before["template"])
        package_id = candidate.id
        package = store.ROOT / "templates" / package_id
        assert package.is_dir() and (package / "manifest.json").is_file()
        # Save the previous library data once, so repeated runs remain harmless.
        backup = REVIEW / "package-before"
        if not backup.exists():
            shutil.copytree(package, backup)
        candidate.name = NAME
        candidate.transition = "cut"
        candidate.caption.enabled = False
        candidate.slot_durations = [
            clip["duration"] for clip in project_before["clips"]
        ]
        candidate.slot_layers = []
        text_layers = [layer for layer in candidate.layers if layer.kind == "text"]
        assert len(text_layers) == 3
        text_layers[0].text = "TÊN SẢN PHẨM"
        text_layers[0].color = "#14334a"
        text_layers[0].background = "#fff1c7"
        text_layers[1].text = "MÔ TẢ SẢN PHẨM"
        text_layers[2].text = "KHÁM PHÁ SẢN PHẨM"
        candidate.notes = (
            "Mẫu review sản phẩm dọc 9:16. Video ở phía trên, thẻ kem trên nền vàng "
            "và biểu tượng giỏ hàng ở phía dưới; tiêu đề xanh than tương phản. "
            "Sửa ba lớp chữ: tên sản phẩm/điểm nổi bật (1–2 dòng), mô tả (tối đa 3 dòng), CTA. "
            "Nhịp gốc: 18 góc trong 18,072 giây, cắt thẳng. Gom các góc cùng hành động "
            "thành cụm 2–3 giây, dùng nguồn/góc khác nhau. Khi tự dựng, các ô co theo "
            "thời lượng audio; kiểm tra lại để mỗi góc khoảng 1 giây và tối đa 3 giây. "
            "Đưa chữ nguồn xuống sau thẻ bằng cắt khung; làm mờ phần còn lộ. "
            "Phụ đề tắt sẵn; có thể bật lại ở phần Phụ đề."
        )
        response = client.post("/api/templates/validate", json=candidate.model_dump())
        response.raise_for_status()
        candidate = Template.model_validate(response.json())
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        for resource in manifest["resources"].values():
            path = package / resource["filename"]
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == resource["sha256"]
        manifest["name"] = NAME
        store.atomic(package / "template.json", candidate.model_dump())
        store.atomic(package / "manifest.json", manifest)
        response = client.get("/api/template-packages")
        response.raise_for_status()
        library = next(item for item in response.json() if item["id"] == package_id)
        assert library["name"] == NAME and not library["missing_assets"]
        assert sum(item["id"] == package_id for item in response.json()) == 1
        thumbnail = client.get(
            f"/api/templates/{package_id}/thumbnail.svg", params={"aspect": "9:16"}
        )
        thumbnail.raise_for_status()
        assert "data:image/png;base64," in thumbnail.text
        (REVIEW / "thumbnail.svg").write_text(thumbnail.text, encoding="utf-8")
        response = client.get(f"/api/projects/{PID}")
        response.raise_for_status()
        assert (
            response.json() == project_before
        ), "Saving the library must not alter the approved project."

    # Verify that the library package can supply its own artwork to a new
    # project even without the original project's asset index.
    actual_root = store.ROOT
    try:
        with tempfile.TemporaryDirectory(
            prefix="clipforge-template-check-"
        ) as temporary:
            store.ROOT = Path(temporary)
            shutil.copytree(package, store.ROOT / "templates" / package_id)
            fresh = Project(name="Kiểm tra mẫu review", aspect="9:16")
            applied = template_packages.apply_package(fresh, package_id)
            assert applied.mode == "template" and applied.template.name == NAME
            image = next(
                layer for layer in applied.template.layers if layer.kind == "image"
            )
            asset = next(
                asset for asset in applied.assets if asset.id == image.asset_id
            )
            assert store.asset_path(applied.id, asset).is_file()
            assert image.asset_id != next(
                layer.asset_id for layer in candidate.layers if layer.kind == "image"
            )
            assert (
                not applied.template.caption.enabled
                and applied.template.transition == "cut"
            )
    finally:
        store.ROOT = actual_root
    result = {
        "package_id": package_id,
        "name": NAME,
        "package_path": str(package.resolve()),
        "slots": len(candidate.slot_durations),
        "reference_duration": sum(candidate.slot_durations),
        "missing_assets": [],
        "project_unchanged": True,
        "cross_project_apply_verified": True,
        "reusable_text_fields": 3,
        "transition": candidate.transition,
        "caption_enabled": candidate.caption.enabled,
    }
    (REVIEW / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
