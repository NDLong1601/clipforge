from __future__ import annotations

import json
import math
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from PIL import Image

from backend import jobs, media, planner, render, store
from backend.main import app
from backend.models import Asset, Cue, Project, Scene, Template
from tests.fixtures.factory import (
    FIXTURE_DIR,
    audio_rms_db,
    load_json_fixture,
    make_media_project,
    make_transparent_logo,
    project_with_scene_count,
)


@pytest.fixture
def m0_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def m0_client(m0_store):
    return TestClient(app)


def test_legacy_project_fixture_loads_with_defaults():
    project = Project.model_validate(load_json_fixture("legacy_project_v1.json"))
    assert project.name == "Dự án ClipForge bản cũ"
    assert project.cue_timing == "estimated"
    assert project.assets == []


def test_corrupt_project_fixture_is_intentionally_invalid():
    with pytest.raises(json.JSONDecodeError):
        json.loads((FIXTURE_DIR / "corrupt_project.json").read_text(encoding="utf-8"))


def test_dynamic_media_fixtures_are_local_and_cover_asset_roles(m0_store):
    project = make_media_project(include_logo=True)
    roles = {asset.role for asset in project.assets}
    assert {"source", "voice", "music", "overlay"} <= roles
    source = next(asset for asset in project.assets if asset.role == "source")
    assert media.probe(store.asset_path(project.id, source))["fps"] == pytest.approx(30, abs=0.1)
    logo = next(asset for asset in project.assets if asset.role == "overlay")
    with Image.open(store.asset_path(project.id, logo)) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema()[0] == 0
    assert store.project_dir(project.id).is_relative_to(m0_store)


def test_two_stale_editor_tabs_get_a_revision_conflict(m0_client):
    copies = load_json_fixture("two_tab_edit.json")
    original = m0_client.post("/api/projects", json={"name": "Hai tab"}).json()
    first = dict(original, name=copies["first_tab_name"])
    stale = dict(original, name=copies["stale_tab_name"])
    assert m0_client.put(f"/api/projects/{original['id']}", json=first).status_code == 200
    response = m0_client.put(f"/api/projects/{original['id']}", json=stale)
    assert response.status_code == 409


def test_regression_template_with_101_scenes_round_trips(m0_store, m0_client, monkeypatch):
    def fake_analyze(_pid, asset, _job=None):
        count = int(asset.duration)
        asset.scenes = [Scene(start=index, end=index + 1) for index in range(count)]
        return asset

    monkeypatch.setattr(media, "analyze", fake_analyze)

    def create_template(scene_count):
        asset = Asset(
            name=f"Video mẫu {scene_count} cảnh",
            filename="reference.mp4",
            role="reference",
            media="video",
            duration=scene_count,
            width=240,
            height=320,
            fps=30,
        )
        project = store.save(Project(name=f"Reference {scene_count}", assets=[asset]))
        response = m0_client.post(
            f"/api/projects/{project.id}/template",
            json={"asset_id": asset.id, "use_ai": False},
        )
        assert response.status_code == 200
        job_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while jobs.JOBS[job_id].status in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert jobs.JOBS[job_id].status == "done", jobs.JOBS[job_id].message
        try:
            return store.read(project.id)
        except ValidationError as exc:
            assert False, f"{scene_count}-scene template must save and reopen: {exc}"

    hundred = create_template(100)
    assert len(hundred.template.slot_durations) == 100
    hundred_one = create_template(101)
    assert len(hundred_one.template.slot_durations) <= 100
    assert sum(hundred_one.template.slot_durations) == pytest.approx(101, abs=0.01)


def test_regression_undo_restores_removed_voice_asset(m0_store):
    voice = Asset(
        id="6666666666666666",
        name="Voice fixture",
        filename="voice.wav",
        role="voice",
        media="audio",
        duration=5,
        has_audio=True,
    )
    project = store.save(Project(name="Undo voice", assets=[voice], voice_id=voice.id))
    project.assets = []
    project.voice_id = ""
    store.save(project)

    restored = store.undo(project.id)
    assert restored.voice_id == voice.id
    assert any(asset.id == restored.voice_id for asset in restored.assets)


def test_regression_audio_gain_is_audible_for_voice_music_and_mix(m0_store):
    base = make_media_project(include_voice=True, include_music=True)
    cases = (
        ("music", "voice", "music_volume"),
        ("voice", "music", "voice_volume"),
        ("both", None, "both"),
    )
    for case, muted_role, volume_field in cases:
        levels = []
        for gain in (0.1, 0.8):
            project = base.model_copy(deep=True)
            if muted_role == "voice":
                project.voice_id = ""
            elif muted_role == "music":
                project.music_id = ""
            if volume_field == "both":
                project.voice_volume = gain
                project.music_volume = gain
            else:
                setattr(project, volume_field, gain)
            result = render.render(project, jobs.Job(project.id, f"gain-{case}-{gain}"), preview=True)
            output = store.project_dir(project.id) / "exports" / result["id"] / result["filename"]
            levels.append(audio_rms_db(output))
        difference = levels[1] - levels[0]
        expected_db = 20 * math.log10(0.8 / 0.1)
        assert difference == pytest.approx(expected_db, abs=1.0), (
            f"{case} gain changed output by {difference:.2f} dB; expected about {expected_db:.2f} dB"
        )
    muted = base.model_copy(deep=True)
    muted.voice_volume = 0
    muted.music_id = ''
    result = render.render(muted, jobs.Job(muted.id, 'gain-muted'), preview=True)
    output = store.project_dir(muted.id) / 'exports' / result['id'] / result['filename']
    assert audio_rms_db(output) < -70


def test_regression_manual_subtitles_survive_replanning(m0_store):
    payload = load_json_fixture("manual_subtitle_edit.json")
    scene = project_with_scene_count(5).assets[0]
    scene.role = "source"
    project = Project(
        name="Subtitle edit",
        script="Câu gốc. Câu tiếp theo.",
        target_duration=5,
        assets=[scene],
        cues=[Cue.model_validate(cue) for cue in payload["cues"]],
        cue_timing=payload["cue_timing"],
        cues_edited=payload["cues_edited"],
    )
    manually_edited = project.cues[0].model_copy(deep=True)
    planned = planner.plan(project, False, jobs.Job(project.id, "replan"))
    assert planned.cues == [manually_edited]


def test_regression_template_logo_survives_cross_project_apply(m0_store, m0_client):
    source_project = Project(name="Dự án nguồn")
    source_dir = store.project_dir(source_project.id) / "assets"
    source_dir.mkdir(parents=True)
    logo_path = make_transparent_logo(source_dir / "logo.png")
    logo = Asset(
        id="4444444444444444",
        name="Logo dự án nguồn",
        filename=logo_path.name,
        role="overlay",
        **media.probe(logo_path),
    )
    original_template = Template.model_validate(load_json_fixture("template_with_logo.json"))
    source_project.assets = [logo]
    source_project.template = original_template
    store.save(source_project)

    package = m0_client.post(f'/api/projects/{source_project.id}/templates', json=original_template.model_dump())
    assert package.status_code == 200, package.text
    target = make_media_project(include_voice=False, include_music=False)
    applied = m0_client.post(f"/api/projects/{target.id}/templates/{package.json()['id']}/apply", json={})
    assert applied.status_code == 200, applied.text
    target = Project.model_validate(applied.json())
    mapped_logo_id = target.template.layers[0].asset_id
    assert mapped_logo_id != logo.id
    assert store.asset_path(target.id, store.get_asset(target, mapped_logo_id)).is_file()
    result = render.render(target, jobs.Job(target.id, 'template-logo'), preview=True)
    assert result['filename'] == 'preview.mp4'


def test_regression_environment_secret_stays_out_of_settings_file(m0_store, monkeypatch):
    secret = "m0-fixture-secret-not-real"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    store.settings()
    store.save_settings({"tts_provider": "windows"})
    persisted = (m0_store / "settings.json").read_text(encoding="utf-8")
    assert secret not in persisted


def test_regression_launcher_finds_running_instance_before_free_port(monkeypatch):
    import launch

    monkeypatch.setattr(launch, "clipforge_running", lambda port: port == 8766)
    monkeypatch.setattr(launch, "port_in_use", lambda port: False)
    assert launch.choose_port() == (8766, True)
