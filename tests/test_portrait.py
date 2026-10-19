"""Whiteboard portrait framing of a finished export."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import jobs, media, portrait, render, store
from backend.main import app
from tests.fixtures.factory import make_media_project
from tests.fixtures.job_helpers import wait_for_job


@pytest.fixture
def exported():
    project = make_media_project(name="Bản dọc vẽ tay")
    info = render.render(project, jobs.Job(project.id, "render"), preview=True)
    project = store.read(project.id)
    project.exports.append(info)
    return store.save(project), info


def submit(client, project, **body):
    return client.post(f"/api/projects/{project.id}/portrait",
                       json={"revision": project.revision, **body})


def test_portrait_export_frames_source_with_narrator_hook_and_music(exported):
    project, source = exported
    client = TestClient(app)
    response = submit(client, project, export_id=source["id"], case="nu",
                      hook_lines=["RỤNG TÓC, KHÓ NGỦ?", "XEM NGAY!"], playback_rate=1.1)
    assert response.status_code == 200, response.text
    job = jobs.JOBS[response.json()["id"]]
    wait_for_job(job, timeout=120)
    assert job.status == "done", job.error

    saved = store.read(project.id)
    entry = next(item for item in saved.exports if item["id"] == job.id)
    assert entry["kind"] == "portrait" and entry["source_export_id"] == source["id"]
    assert entry["case"] == "nu" and entry["hook"] == ["RỤNG TÓC, KHÓ NGỦ?", "XEM NGAY!"]
    directory = store.project_dir(project.id) / "exports" / job.id
    info = media.probe(directory / portrait.OUTPUT)
    assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"]
    # 5 s source at x1.1 keeps every frame of narration: ceil(150 / 1.1) = 137 frames.
    assert abs(info["duration"] - 137 / 30) < 0.1
    assert (directory / portrait.COVER).is_file() and not (directory / "work").exists()
    assert client.get(f"/api/projects/{project.id}/exports/{job.id}/{portrait.OUTPUT}").status_code == 200


def test_portrait_request_validation(exported):
    project, source = exported
    client = TestClient(app)
    assert submit(client, project, export_id="0" * 16).status_code == 422
    assert submit(client, project, export_id=source["id"], case="doi-thoai").status_code == 422
    assert submit(client, project, export_id=source["id"], playback_rate=1.5).status_code == 422
    assert submit(client, project, export_id=source["id"], hook_lines=["x" * 61]).status_code == 422
    assert submit(client, project, export_id=source["id"], hook_lines=["a", "b", "c", "d"]).status_code == 422
    stale = client.post(f"/api/projects/{project.id}/portrait",
                        json={"revision": project.revision - 1, "export_id": source["id"]})
    assert stale.status_code == 409


def test_portrait_cannot_frame_a_portrait_export():
    with pytest.raises(ValueError):
        portrait.options({}, {"id": "a" * 16, "kind": "portrait"})


@pytest.mark.parametrize("size, box", [((1920, 1080), (0, 620, 1080, 608)),
                                       ((720, 1280), (240, 540, 600, 1066)),
                                       ((1080, 1080), (140, 560, 800, 800))])
def test_source_box_follows_source_aspect(size, box):
    assert portrait.source_box(*size) == box


def test_narrator_cels_are_cut_from_both_sheets():
    for case in portrait.CASES:
        states = portrait.narrator_states(case)
        assert len(states) == 4
        assert all(state.size == portrait.NARRATOR_RECT[2:] and state.getbbox() for state in states)
