"""Edit the existing Vitamin project to 22 seconds using its supplied narration.

Preserves the source files, uses pitch-preserving tempo, and selects source
angles against the supplied speech segments and decoded footage.
"""

from contextlib import ExitStack
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.media import probe, run_ff
from backend.models import Clip
from backend.timeline_math import timeline_duration

PID = "5556f14238b84591"
REVIEW = ROOT / "review" / "vitamin-22s"
FOLDER = Path(
    r"C:\Users\PC\Downloads\Vitamine tổng hợp-20261001T102539Z-1-001\Vitamine tổng hợp"
)
TARGET = 22.0


def prepare_media():
    REVIEW.mkdir(parents=True, exist_ok=True)
    original = probe(FOLDER / "audio.mp3")["duration"]
    tempo = original / TARGET
    run_ff(
        [
            "-i",
            FOLDER / "audio.mp3",
            "-vn",
            "-af",
            f"atempo={tempo:.10f},apad,atrim=duration={TARGET},asetpts=PTS-STARTPTS",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            REVIEW / "audio-22s.wav",
        ]
    )
    images = [
        ("B5-motion.mp4", "provitamin_b5_la_gi_cong_dung_provitamin_b5_672332d5fe.png"),
        ("B2-motion.mp4", "b2.jpg"),
        ("B9-motion.mp4", "vitamin-b9.webp"),
    ]
    for name, source in images:
        # Fit the supplied illustration above the banner. The slow zoom makes
        # these intended graphic shots move without cropping vitamin labels.
        vf = (
            "scale=540:620:force_original_aspect_ratio=decrease,"
            "pad=576:1024:(ow-iw)/2:20:color=white,"
            "zoompan=z='min(1.045,1+on*0.0006)':x='iw/2-iw/zoom/2':y='0':d=60:s=576x1024:fps=30"
        )
        run_ff(
            [
                "-i",
                FOLDER / source,
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
                REVIEW / name,
            ]
        )
    run_ff(
        [
            "-ss",
            "22.8",
            "-t",
            "2.1",
            "-i",
            FOLDER / "B7.mp4",
            "-an",
            "-vf",
            "crop=432:768:150:430,scale=576:1024,fps=30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            REVIEW / "B7-pill-closeup.mp4",
        ]
    )
    assert abs(probe(REVIEW / "audio-22s.wav")["duration"] - TARGET) < 0.001
    return tempo


def main():
    tempo = prepare_media()
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=120) as client:

        def request(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        def wait_job(job):
            started = time.monotonic()
            while True:
                current = next(
                    j
                    for j in request("GET", "/api/jobs", params={"pid": PID})
                    if j["id"] == job["id"]
                )
                if current["status"] == "done":
                    return current
                if current["status"] in ("error", "cancelled"):
                    raise ValueError(current["message"])
                if time.monotonic() - started > 240:
                    raise TimeoutError(current)
                time.sleep(0.7)

        def upload(names, role):
            with ExitStack() as stack:
                files = [
                    (
                        "files",
                        (
                            name,
                            stack.enter_context((REVIEW / name).open("rb")),
                            "audio/wav" if role == "voice" else "video/mp4",
                        ),
                    )
                    for name in names
                ]
                result = request(
                    "POST",
                    f"/api/projects/{PID}/assets",
                    files=files,
                    data={"role": role, "auto_assemble": "false"},
                )
            wait_job(result["job"])

        url = f"/api/projects/{PID}"
        p = request("GET", url)
        original_path = REVIEW / "project-before.json"
        if not original_path.exists():
            original_path.write_text(
                json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        base = json.loads(original_path.read_text(encoding="utf-8"))
        assert (
            p["script"] == base["script"]
        ), "Narration changed while preparing this edit."
        assert not any(
            clip["locked"] for clip in p["clips"]
        ), "Keep locked shots before replacing the timeline."
        generated = [
            "B2-motion.mp4",
            "B5-motion.mp4",
            "B9-motion.mp4",
            "B7-pill-closeup.mp4",
        ]
        missing = [
            name
            for name in generated
            if not any(a["name"] == name and not a["deleted"] for a in p["assets"])
        ]
        if missing:
            upload(missing, "source")
        if not any(
            a["name"] == "audio-22s.wav" and not a["deleted"] for a in p["assets"]
        ):
            upload(["audio-22s.wav"], "voice")
        p = request("GET", url)
        p["voice_id"] = next(
            a["id"]
            for a in p["assets"]
            if a["name"] == "audio-22s.wav" and not a["deleted"]
        )
        p["target_duration"] = TARGET
        p["cues"] = [
            {
                **cue,
                "start": round(cue["start"] / tempo, 6),
                "end": round(cue["end"] / tempo, 6),
            }
            for cue in base["cues"]
        ]
        p["cue_timing"] = base["cue_timing"]
        p["cues_edited"] = True
        p["cues_stale"] = False
        p["smooth_transitions"] = False
        p["source_text_mode"] = "blur"
        p["source_volume"] = 0
        p["resolution"] = "1080"
        p["aspect"] = "9:16"
        layers = [layer for layer in p["template"]["layers"] if layer["kind"] == "text"]
        layers[0].update(
            text="PENTAVITE B PLUS\n8 LOẠI VITAMIN NHÓM B",
            color="#14334a",
            background="#fff1c7",
        )
        layers[1][
            "text"
        ] = "8 loại vitamin nhóm B.\nThiết kế lọ nhỏ gọn.\nXem hướng dẫn trên nhãn."
        if len(layers) > 2:
            layers[2]["text"] = "KHÁM PHÁ PENTAVITE"
        p["template"]["caption"]["enabled"] = False
        p["template"]["transition"] = "cut"

        # The seven following group boundaries come from the supplied ASR.
        # Related short angles stay together; cuts enter the next topic near
        # the next spoken segment rather than at arbitrary one-second beats.
        boundaries = [0] + [cue["start"] / tempo for cue in base["cues"][1:]] + [TARGET]
        # Each tuple: source, seek time, crop_y. Product subtitles above the
        # hands are excluded by crop, keeping product labels readable.
        groups = [
            [("rụng tóc.mp4", 0.35, 0.15), ("rụng tóc.mp4", 8.9, 0.1)],
            [("mất ngủ.mp4", 0.15, 0.25), ("B5-motion.mp4", 0, 0)],
            [("nhiệt miệng.mp4", 42, 0.1), ("B2-motion.mp4", 0, 0)],
            [("buồn nôn.mp4", 0.3, 0.1), ("B9-motion.mp4", 0, 0)],
            [
                ("B2-motion.mp4", 0.15, 0),
                ("B5-motion.mp4", 0.15, 0),
                ("B7-pill-closeup.mp4", 0.1, 0.1),
                ("B9-motion.mp4", 0.15, 0),
            ],
            [
                ("sản phẩm.mp4", 27.4, 0.55),
                ("pentavite.mp4", 26.75, 0.2),
                ("sản phẩm.mp4", 5.4, 0.85),
                ("pentavite.mp4", 38.55, 0.65),
                ("sản phẩm.mp4", 7.95, 0.85),
            ],
            [("sản phẩm.mp4", 29, 0.55), ("pentavite.mp4", 48.1, 0.45)],
            [("pentavite.mp4", 49.95, 0.45), ("sản phẩm.mp4", 0.5, 0.9)],
        ]
        titles = [
            "B7 / tóc",
            "B5",
            "B2",
            "B9",
            "Các vitamin nhóm B",
            "Pentavite B Plus",
            "Cận cảnh Pentavite",
            "Sản phẩm Pentavite",
        ]
        clips = []
        timeline = []
        position = 0.0
        for group_index, shots in enumerate(groups):
            length = (boundaries[group_index + 1] - boundaries[group_index]) / len(
                shots
            )
            for name, start, crop_y in shots:
                asset = next(
                    a for a in p["assets"] if a["name"] == name and not a["deleted"]
                )
                scene = next(
                    (s for s in asset["scenes"] if s["start"] <= start < s["end"]), None
                )
                speed = 1 if name.endswith(("motion.mp4", "closeup.mp4")) else 1.12
                available = (scene["end"] if scene else asset["duration"]) - start
                assert available >= length * speed + 0.01, (
                    name,
                    start,
                    available,
                    length,
                )
                clip = Clip(
                    asset_id=asset["id"],
                    scene_id=scene["id"] if scene else "",
                    source_start=start,
                    duration=round(length, 6),
                    speed=speed,
                    crop_x=0.5,
                    crop_y=crop_y,
                    fit="cover",
                    transition="cut",
                    title=titles[group_index],
                    text_mode="off",
                )
                clips.append(clip)
                timeline.append(
                    {
                        "start": round(position, 6),
                        "duration": clip.duration,
                        "group": titles[group_index],
                        "source": name,
                        "source_start": start,
                        "speed": speed,
                        "crop_y": crop_y,
                    }
                )
                position += clip.duration
        clips[-1].duration = round(
            clips[-1].duration + TARGET - timeline_duration(clips), 6
        )
        assert abs(timeline_duration(clips) - TARGET) < 0.000001
        assert max(clip.duration for clip in clips) < 2
        p["clips"] = [clip.model_dump() for clip in clips]
        p["template"]["slot_durations"] = [clip.duration for clip in clips]
        p = request("PUT", url, json=p)
        preflight = request("GET", url + "/preflight")
        assert preflight["ok"] and not preflight["issues"], preflight
        job = request("POST", url + "/render", json={"preview": True})
        result = {
            "project_id": PID,
            "revision": p["revision"],
            "preview_job": job["id"],
            "duration": TARGET,
            "audio_original_duration": probe(FOLDER / "audio.mp3")["duration"],
            "audio_tempo": tempo,
            "pitch_preserved": True,
            "clips": len(clips),
            "preflight": preflight,
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
