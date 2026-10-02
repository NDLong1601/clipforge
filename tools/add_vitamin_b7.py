"""Insert the supplied B7 artwork at both B7 cues in the Vitamin edit."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.media import probe, run_ff
from backend.models import Project, uid
from backend.timeline_math import clip_starts, timeline_duration

PID = "5556f14238b84591"
REVIEW = ROOT / "review" / "vitamin-b7"
SOURCE = Path(r"C:\Users\PC\Downloads\B7.avif")


def main():
    REVIEW.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as picture:
        picture.convert("RGB").save(REVIEW / "B7.png")
    # Keep the entire artwork above the card, using the same gentle motion as
    # the existing B2/B5/B9 illustrations. AVIF is decoded without changing it.
    vf = (
        "scale=540:620:force_original_aspect_ratio=decrease,"
        "pad=576:1024:(ow-iw)/2:20:color=white,"
        "zoompan=z='min(1.045,1+on*0.0006)':x='iw/2-iw/zoom/2':y='0':d=60:s=576x1024:fps=30"
    )
    motion = REVIEW / "B7-motion.mp4"
    run_ff(
        [
            "-i",
            REVIEW / "B7.png",
            "-vf",
            vf,
            "-t",
            "2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            motion,
        ]
    )
    assert probe(motion)["duration"] >= 2
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=120) as client:
        url = f"/api/projects/{PID}"

        def request(method, route, **kwargs):
            response = client.request(method, route, **kwargs)
            response.raise_for_status()
            return response.json()

        before = request("GET", url)
        assert len(before["clips"]) in (21, 22)
        assert abs(timeline_duration(Project.model_validate(before).clips) - 22) < 0.001
        snapshot = REVIEW / "project-before.json"
        if not snapshot.exists():
            snapshot.write_text(
                json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        asset = next(
            (
                a
                for a in before["assets"]
                if a["name"] == motion.name and not a["deleted"]
            ),
            None,
        )
        if asset is None:
            with motion.open("rb") as stream:
                upload = request(
                    "POST",
                    url + "/assets",
                    files=[("files", (motion.name, stream, "video/mp4"))],
                    data={"role": "source", "auto_assemble": "false"},
                )
            deadline = time.monotonic() + 120
            while True:
                job = next(
                    j
                    for j in request("GET", "/api/jobs", params={"pid": PID})
                    if j["id"] == upload["job"]["id"]
                )
                if job["status"] == "done":
                    break
                if job["status"] in ("error", "cancelled"):
                    raise RuntimeError(job["message"])
                if time.monotonic() > deadline:
                    raise TimeoutError(job)
                time.sleep(0.5)
        p = request("GET", url)
        assert (
            p["clips"] == before["clips"]
        ), "Timeline changed during media preparation."
        asset = next(
            a for a in p["assets"] if a["name"] == motion.name and not a["deleted"]
        )
        for index in (1, 10):
            clip = p["clips"][index]
            assert not clip["locked"], "Preserve a locked shot."
            clip.update(
                asset_id=asset["id"],
                scene_id="",
                source_start=0 if index == 1 else 0.15,
                speed=1,
                crop_x=0.5,
                crop_y=0,
                fit="cover",
                text_mode="off",
                text_regions=[],
                text_regions_override=False,
            )
        # Align the quick vitamin list against the checked audio mentions.
        # B7 is spoken near 10.05–10.35s, earlier than its previous shot.
        # Add a related pill angle after B9 to retain the group's full length.
        if len(p["clips"]) == 22:
            assert p["clips"][12]["title"] == "Các vitamin nhóm B · Cận viên"
            p["clips"].pop(12)
        list_start = sum(c["duration"] for c in p["clips"][:8])
        list_end = sum(c["duration"] for c in p["clips"][:12])
        boundaries = [list_start, 9.25, 9.85, 10.45, 11.45, list_end]
        for offset in range(4):
            p["clips"][8 + offset]["duration"] = round(
                boundaries[offset + 1] - boundaries[offset], 6
            )
        pill = next(
            a
            for a in p["assets"]
            if a["name"] == "B7-pill-closeup.mp4" and not a["deleted"]
        )
        extra = copy.deepcopy(p["clips"][10])
        extra.update(
            id=uid(),
            asset_id=pill["id"],
            source_start=0.1,
            crop_y=0.1,
            duration=round(list_end - 11.45, 6),
            title="Các vitamin nhóm B · Cận viên",
        )
        p["clips"].insert(12, extra)
        p["template"]["slot_durations"] = [c["duration"] for c in p["clips"]]
        assert abs(timeline_duration(Project.model_validate(p).clips) - 22) < 0.001
        assert max(c["duration"] for c in p["clips"]) < 2
        assert p["clips"][:1] == before["clips"][:1]
        assert p["clips"][2:8] == before["clips"][2:8]
        assert (
            p["clips"][13:]
            == before["clips"][12 if len(before["clips"]) == 21 else 13 :]
        )
        assert {k: v for k, v in p["template"].items() if k != "slot_durations"} == {
            k: v for k, v in before["template"].items() if k != "slot_durations"
        }
        for key in (
            "voice_id",
            "music_id",
            "script",
            "cues",
            "voice_volume",
            "music_volume",
            "target_duration",
        ):
            assert p[key] == before[key], key
        p = request("PUT", url, json=p)
        preflight = request("GET", url + "/preflight")
        assert preflight["ok"] and not preflight["issues"], preflight
        parsed = Project.model_validate(p)
        starts = clip_starts(parsed.clips)
        timeline = []
        for index, clip in enumerate(parsed.clips):
            source = next(a for a in parsed.assets if a.id == clip.asset_id)
            timeline.append(
                {
                    "start": starts[index],
                    "duration": clip.duration,
                    "source": source.name,
                    "source_start": clip.source_start,
                    "speed": clip.speed,
                    "crop_y": clip.crop_y,
                    "group": clip.title,
                }
            )
        job = request("POST", url + "/render", json={"preview": True})
        result = {
            "project_id": PID,
            "revision": p["revision"],
            "duration": 22,
            "clips": len(parsed.clips),
            "preview_job": job["id"],
            "preflight": preflight,
            "source_image": str(SOURCE),
            "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "image_shots": [2, 11],
            "b7_windows": [[1.094061, 2.188122], [9.85, 10.45]],
            "delivery_filename": "Vitamin_Pentavite_22s_B7.mp4",
            "previous_audio_reference": str(
                ROOT / "review/vitamin-22s/Vitamin_Pentavite_22s.mp4"
            ),
            "timeline": timeline,
            "cues": p["cues"],
        }
        (REVIEW / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in ("timeline", "cues")},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
