"""Hand-drawn whiteboard mode: kit import, drawing export, storyboard and AI images."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend import jobs, media, store
from backend.main import app
from backend.models import Asset, Cue, Project, WBScene, WBTitle
from backend.whiteboard import engine, regions, storyboard
from tests.fixtures.job_helpers import wait_for_job

BACKGROUND = (246, 241, 227)


def drawing(size=(1200, 800)):
    """Two separate cartoon objects on white, like a generated illustration."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((80, 200, 420, 560), outline=(43, 42, 40), width=14, fill=(125, 157, 168))
    draw.rectangle((760, 160, 1080, 620), outline=(43, 42, 40), width=14, fill=(172, 84, 55))
    return image


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def voice_bytes(tmp_path, seconds=3):
    path = tmp_path / "voice.wav"
    media.run_ff(["-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", str(seconds),
                  "-c:a", "pcm_s16le", path])
    return path.read_bytes()


def kit_zip(tmp_path):
    product = io.BytesIO()
    Image.new("RGB", (400, 400), (40, 160, 60)).save(product, "JPEG")
    sheet = Image.new("RGB", (2400, 800), "white")
    sheet.paste(drawing(), (0, 0))
    sheet.paste(drawing().transpose(Image.Transpose.FLIP_LEFT_RIGHT), (1200, 0))
    cfg = {
        "schema_version": 1, "case": "nu", "name": "kiem-tra", "voice": "assets/voice.wav",
        "product_original": "assets/product.jpg", "music": None,
        "video": {"width": 1920, "height": 1080, "fps": 30, "background": "#F6F1E3"},
        "story_character": {"reference": "assets/story-character/reference.png"},
        "transition_frames": 4, "overlays": [{"image": "assets/product.jpg", "start": 1.6, "end": 3.0,
                                              "rect": [100, 600, 300, 300]}],
        "scenes": [
            {"id": "01-mo-dau", "start_frame": 0, "end_frame": 48, "image": "assets/sheet.png",
             "crop": [0, 0, 0.5, 1], "image_box": [990, 60, 860, 860], "narration": "Rụng tóc",
             "has_story_character": True,
             "elements": [{"id": "hero", "region": {"x": 990, "y": 60, "width": 860, "height": 860},
                           "reveal": {"startMs": 0, "durationMs": 1000}}],
             "titles": [{"text": "RỤNG TÓC?", "start": 0.4, "end": 1.6, "color": "#2B2A28"},
                        {"text": "XEM NGAY!", "start": 0.9, "end": 1.6, "color": "#AC5437"}]},
            {"id": "02-san-pham", "start_frame": 48, "end_frame": 90, "image": "assets/sheet.png",
             "crop": [0.5, 0, 1, 1], "image_box": [990, 60, 860, 860], "narration": "Dùng thử",
             "has_story_character": False,
             "elements": [{"id": "pack", "region": {"x": 990, "y": 60, "width": 860, "height": 860},
                           "reveal": {"startMs": 0, "durationMs": 800}}], "titles": []},
        ],
    }
    path = tmp_path / "kit.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("du-an/project.json", json.dumps(cfg, ensure_ascii=False))
        archive.writestr("du-an/assets/voice.wav", voice_bytes(tmp_path))
        archive.writestr("du-an/assets/product.jpg", product.getvalue())
        archive.writestr("du-an/assets/sheet.png", png(sheet))
        archive.writestr("du-an/transcript.json", json.dumps({"segments": [
            {"start": 0.1, "end": 1.5, "text": "Rụng tóc"}, {"start": 1.7, "end": 2.9, "text": "Dùng thử"}]}))
    return path


def import_kit(client, tmp_path):
    project = client.post("/api/projects", json={"name": "Vẽ tay"}).json()
    with kit_zip(tmp_path).open("rb") as stream:
        response = client.post(f"/api/projects/{project['id']}/whiteboard/import",
                               files={"file": ("kit.zip", stream, "application/zip")},
                               data={"revision": str(project["revision"])})
    assert response.status_code == 200, response.text
    return response.json()


def test_kit_zip_import_keeps_layout_timing_and_media(tmp_path):
    client = TestClient(app)
    project = import_kit(client, tmp_path)
    board = project["whiteboard"]
    assert project["mode"] == "whiteboard" and board["case"] == "nu" and board["aspect"] == "16:9"
    assert board["transition_frames"] == 4 and project["voice_id"] and board["product_id"]
    first, second = board["scenes"]
    assert (first["start"], second["start"], second["end"]) == (0, 1.6, 3.0)
    assert first["crop"] == {"x": 0, "y": 0, "w": 0.5, "h": 1}
    assert first["layout"]["image_box"]["x"] == pytest.approx(990 / 1920)
    assert first["layout"]["elements"][0]["duration_ms"] == 1000
    assert [(t["text"], t["accent"], t["start"]) for t in first["titles"]] == [
        ("RỤNG TÓC?", False, 0.4), ("XEM NGAY!", True, pytest.approx(0.9))]
    assert first["has_character"] and second["product"] and not first["product"]
    assert first["image_id"] == second["image_id"]  # one sheet, two crops
    assert [cue["text"] for cue in project["cues"]] == ["Rụng tóc", "Dùng thử"]
    assert any("nhân vật khóa" in warning for warning in project["warnings"])
    roles = {asset["role"] for asset in project["assets"]}
    assert {"voice", "product", "illustration"} <= roles


def test_whiteboard_export_draws_every_scene_with_voice_and_captions(tmp_path):
    client = TestClient(app)
    project = import_kit(client, tmp_path)
    sid = project["whiteboard"]["scenes"][0]["id"]
    preview = client.get(f"/api/projects/{project['id']}/whiteboard/scenes/{sid}/preview.png")
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
    response = client.post(f"/api/projects/{project['id']}/whiteboard/render", json={"revision": project["revision"]})
    assert response.status_code == 200, response.text
    job = wait_for_job(jobs.JOBS[response.json()["id"]], timeout=300)
    assert job.status == "done", job.error
    entry = next(item for item in store.read(project["id"]).exports if item["id"] == job.id)
    assert entry["kind"] == "whiteboard" and entry["scene_count"] == 2 and entry["clip_cache_misses"] == 2
    directory = store.project_dir(project["id"]) / "exports" / job.id
    info = media.probe(directory / "video.mp4")
    assert (info["width"], info["height"]) == (1920, 1080) and info["has_audio"]
    assert abs(info["duration"] - 3.0) < 0.1
    assert "Rụng tóc" in (directory / "captions.srt").read_text(encoding="utf-8")
    assert not (directory / "work").exists()
    # The drawing is finished before each cut: the last frame of scene 1 has ink.
    import av
    with av.open(str(directory / "video.mp4")) as container:
        frames = [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]
    assert len(frames) == 90
    finished, opening = regions.ink_mask(frames[45], BACKGROUND).mean(), regions.ink_mask(frames[0], BACKGROUND).mean()
    assert finished > 0.03 and opening < finished / 3  # only the hand is on the board at first
    # A second export reuses every drawn scene clip.
    response = client.post(f"/api/projects/{project['id']}/whiteboard/render", json={})
    again = wait_for_job(jobs.JOBS[response.json()["id"]], timeout=300)
    assert again.status == "done", again.error
    entry = next(item for item in store.read(project["id"]).exports if item["id"] == again.id)
    assert entry["clip_cache_hits"] == 2


def test_auto_regions_follow_reading_order_and_cover_the_ink():
    canvas = Image.new("RGB", (1920, 1080), BACKGROUND)
    canvas.paste(drawing(), (300, 100))
    pixels = np.array(canvas)
    found = regions.find(pixels, BACKGROUND, (64, 16, 1792, 884))
    assert len(found) == 2 and found[0][0] < found[1][0]
    assert regions.uncovered(pixels, found)[0] <= regions.MAX_UNCOVERED
    timing = regions.timing(found, 2400)
    assert timing[0][0] == 0 and timing[1][0] == sum(timing[0])
    assert sum(timing[-1]) <= 2400 - 500


def test_flatten_turns_a_white_backdrop_into_the_board_colour():
    flat = np.array(engine._flatten(drawing(), BACKGROUND))
    assert tuple(flat[5, 5]) == BACKGROUND
    assert tuple(flat[380, 250]) != BACKGROUND


def whiteboard_project(tmp_path, scenes=2, with_images=True):
    project = Project(name="Kịch bản vẽ tay", mode="whiteboard", script="Một. Hai. Ba.")
    directory = store.project_dir(project.id) / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "voice.wav").write_bytes(voice_bytes(tmp_path, 4))
    voice = Asset(name="Giọng", filename="voice.wav", role="voice", **media.probe(directory / "voice.wav"))
    project.assets.append(voice)
    project.voice_id = voice.id
    project.cues = [Cue(start=0, end=1.3, text="Rụng tóc, khó ngủ."), Cue(start=1.3, end=2.6, text="Đau đầu."),
                    Cue(start=2.6, end=4, text="Dùng thử ngay!")]
    for index in range(scenes):
        image_id = ""
        if with_images:
            (directory / f"art{index}.png").write_bytes(png(drawing()))
            asset = Asset(name=f"Tranh {index}", filename=f"art{index}.png", role="illustration",
                          **media.probe(directory / f"art{index}.png"))
            project.assets.append(asset)
            image_id = asset.id
        project.whiteboard.scenes.append(WBScene(start=index * 2, end=index * 2 + 2, image_id=image_id,
                                                 titles=[WBTitle(text="TỪ KHÓA")], has_character=index == 0))
    return store.save(project)


def test_render_refuses_scenes_without_art_or_too_short(tmp_path):
    client = TestClient(app)
    project = whiteboard_project(tmp_path, with_images=False)
    response = client.post(f"/api/projects/{project.id}/whiteboard/render", json={})
    assert response.status_code == 400 and "chưa có tranh" in response.json()["detail"]
    project = whiteboard_project(tmp_path)
    project.whiteboard.scenes[1].start = 3.8
    project = store.save(project)
    response = client.post(f"/api/projects/{project.id}/whiteboard/render", json={})
    assert response.status_code == 400 and "quá ngắn" in response.json()["detail"]


def test_storyboard_without_ai_splits_cues_into_contiguous_scenes(tmp_path):
    client = TestClient(app)
    project = whiteboard_project(tmp_path, scenes=0)
    response = client.post(f"/api/projects/{project.id}/whiteboard/plan", json={"use_ai": False})
    job = wait_for_job(jobs.JOBS[response.json()["id"]], timeout=30)
    assert job.status == "done", job.error
    board = store.read(project.id).whiteboard
    assert board.scenes and board.scenes[0].start == 0 and board.scenes[-1].end == pytest.approx(4, abs=0.01)
    assert all(a.end == b.start for a, b in zip(board.scenes, board.scenes[1:]))
    assert board.scenes[0].titles and board.character_description


def test_ai_images_lock_a_character_reference_and_reuse_it(tmp_path, monkeypatch):
    store.save_settings({"ai_profiles": [{"id": "img", "name": "OpenAI", "provider": "openai", "api_key": "fake-key",
                                          "model": "gpt-4o-mini", "base_url": "", "enabled": True}],
                         "active_ai_profile_id": "img"})
    project = whiteboard_project(tmp_path, with_images=False)
    calls = []

    class Images:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            body = {"data": [{"b64_json": base64.b64encode(png(drawing((512, 512)))).decode()}]}
            return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(storyboard, "client_factory", Images)
    client = TestClient(app)
    response = client.post(f"/api/projects/{project.id}/whiteboard/images", json={})
    job = wait_for_job(jobs.JOBS[response.json()["id"]], timeout=60)
    assert job.status == "done", job.error
    saved = store.read(project.id)
    board = saved.whiteboard
    assert board.character_reference_id and all(scene.image_id for scene in board.scenes)
    assert [url.rsplit("/", 2)[-2:] for url, _ in calls] == [
        ["images", "generations"], ["images", "edits"], ["images", "generations"]]
    assert calls[0][1]["json"]["size"] == "1024x1024" and calls[2][1]["json"]["size"] == "1536x1024"
    assert "LOCKED story character" in calls[1][1]["data"]["prompt"]
    assert "No main character" in calls[2][1]["json"]["prompt"]
    assert {saved.assets[-1].role, saved.assets[-2].role} == {"illustration"}
    # Removing an illustration clears the scenes that used it.
    image_id = board.scenes[0].image_id
    after = client.delete(f"/api/projects/{project.id}/assets/{image_id}").json()
    assert after["whiteboard"]["scenes"][0]["image_id"] == ""


def test_jpeg_images_register_with_a_thumbnail(tmp_path):
    project = store.save(Project(name="JPEG"))
    path = store.project_dir(project.id) / "assets" / "photo.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 200), (200, 30, 30)).save(path, "JPEG")
    asset = media.register(project.id, path, "photo.jpg", "product")
    assert (store.project_dir(project.id) / "thumbs" / asset.thumbnail).is_file()


def test_ai_storyboard_snaps_scenes_to_cues_and_keeps_keywords(tmp_path, monkeypatch):
    project = whiteboard_project(tmp_path, scenes=0)
    prompts = []

    def ai_json(prompt, **kwargs):
        prompts.append(prompt)
        return {"character": "Woman in her 30s, short bob hair, mustard cardigan",
                "scenes": [{"from": 0, "to": 0, "titles": ["RỤNG TÓC?", "KHÓ NGỦ?"], "accent": 1,
                            "illustration": "tired woman on a bed", "has_character": True},
                           {"from": 2, "to": 2, "titles": ["DÙNG THỬ"], "product": True, "has_character": False}]}

    monkeypatch.setattr(storyboard.providers, "ai_json", ai_json)
    response = TestClient(app).post(f"/api/projects/{project.id}/whiteboard/plan", json={"use_ai": True})
    job = wait_for_job(jobs.JOBS[response.json()["id"]], timeout=30)
    assert job.status == "done", job.error
    board = store.read(project.id).whiteboard
    assert [(s.start, s.end) for s in board.scenes] == [(0, 2.6), (2.6, pytest.approx(4, abs=0.01))]
    assert board.scenes[0].narration == "Rụng tóc, khó ngủ. Đau đầu."  # skipped cue joins the scene before
    assert [(t.text, t.accent) for t in board.scenes[0].titles] == [("RỤNG TÓC?", False), ("KHÓ NGỦ?", True)]
    assert board.scenes[0].image_prompt == "tired woman on a bed" and not board.scenes[1].product  # no product photo
    assert board.character_description.startswith("Woman in her 30s")
    assert "Nhân vật trong tranh là male" in prompts[0]
