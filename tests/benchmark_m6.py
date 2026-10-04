"""Measure cold, warm, interrupted/resumed rendering and busy-queue API latency.

Write the JSON report outside the repository with ``--output``. The report omits
local paths, Git identity, prompts, project names, and media contents.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from backend import jobs, media, render, store
from backend.jobs import Job
from backend.models import Asset, Clip, Project
from tests.benchmark_baseline import PeakMemorySampler, _tree_bytes
from tests.fixtures.factory import make_media_project


def _percentile_ms(samples: list[float], percentile: float = 0.95) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index] * 1000, 2)


def _api_latency() -> dict:
    from backend.main import app
    from backend import processes

    jobs.JOBS.clear()
    jobs._RUNTIME_HANDLERS.clear()
    jobs.update_scheduler(paused=True)
    total = 110
    for index in range(total):
        job = jobs.reserve(f"{index + 1:016x}", "render")
        jobs.launch(job, lambda _job: None)

    client = TestClient(app)
    project = store.save(Project())
    encoding = jobs.reserve(project.id, "render")

    def worker(job):
        job.start_step("encode-video")
        media.run_ff(["-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                      "-t", "8", "-an", "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast",
                      store.project_dir(project.id) / "api-load.mp4"], job=job)

    jobs.launch(encoding, worker)
    with jobs.LOCK:
        jobs._dispatch_one_locked(encoding)
        future = encoding._future
    deadline = time.monotonic() + 8
    while not (encoding.encode_progress or {}).get("processed_seconds"):
        if future.done() or time.monotonic() > deadline:
            raise RuntimeError("Native API load did not start")
        time.sleep(0.05)
    progress_before = encoding.encode_progress["processed_seconds"]
    health_samples = []
    queue_samples = []
    for _ in range(30):
        if encoding.status != "running" or processes.active_count() < 1:
            raise RuntimeError("Latency sample was taken without native work running")
        started = time.perf_counter()
        response = client.get("/api/health")
        health_samples.append(time.perf_counter() - started)
        if response.status_code != 200:
            raise RuntimeError(f"Health endpoint returned HTTP {response.status_code}")

        started = time.perf_counter()
        response = client.get("/api/jobs/queue")
        queue_samples.append(time.perf_counter() - started)
        if response.status_code != 200 or len(response.json().get("jobs", [])) != total + 1:
            raise RuntimeError("Queue endpoint lost active jobs under benchmark load")
        time.sleep(0.05)

    progress_after = encoding.encode_progress["processed_seconds"]
    if progress_after <= progress_before:
        raise RuntimeError("Native encode did not advance during API measurements")
    future.result(timeout=15)

    return {
        "active_jobs": total + 1,
        "queued_jobs": total,
        "native_encodes_running_during_every_sample": 1,
        "processed_seconds_before": progress_before,
        "processed_seconds_after": progress_after,
        "samples_per_endpoint": len(health_samples),
        "health_p95_ms": _percentile_ms(health_samples),
        "queue_p95_ms": _percentile_ms(queue_samples),
    }


def benchmark(output: Path) -> dict:
    output = output.resolve()
    if output == PROJECT_ROOT or PROJECT_ROOT in output.parents:
        raise ValueError("Benchmark report must be written outside the repository")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="clipforge-m6-") as temporary:
        root = Path(temporary)
        data_root = root / "data"
        store.ROOT = data_root
        jobs.ROOT = data_root
        jobs.JOBS.clear()
        jobs._RUNTIME_HANDLERS.clear()
        jobs._SCHEDULER_PAUSED = True

        project = Project(
            name="M6 synthetic benchmark",
            target_duration=5,
            resolution="720",
            aspect="9:16",
        )
        project.template.caption.enabled = False
        asset_dir = store.project_dir(project.id) / "assets"
        asset_dir.mkdir(parents=True)
        video_path = asset_dir / "synthetic_60s.mp4"
        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            media.run_ff([
                "-f", "lavfi", "-i", "testsrc2=size=240x426:rate=15",
                "-t", "60", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "30", "-pix_fmt", "yuv420p", video_path,
            ])
            generate_seconds = time.perf_counter() - started
        generate_peak_mb = memory.peak_mb

        asset = Asset(
            name="Synthetic 60 second source",
            filename=video_path.name,
            role="source",
            **media.probe(video_path),
        )
        project.assets.append(asset)
        project = store.save(project)
        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            media.analyze(project.id, asset)
            analyze_seconds = time.perf_counter() - started
        analyze_peak_mb = memory.peak_mb
        project.assets[0] = asset
        project.clips = [Clip(
            asset_id=asset.id,
            scene_id=asset.scenes[0].id,
            source_start=asset.scenes[0].start,
            duration=5,
        )]
        project = store.save(project)

        cold_job = Job(project.id, "render", input_fingerprint="m6-benchmark")
        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            cold_result = render.render(project, cold_job, preview=True)
            cold_seconds = time.perf_counter() - started
        cold_peak_mb = memory.peak_mb

        warm_job = Job(project.id, "render", input_fingerprint="m6-benchmark")
        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            warm_result = render.render(project, warm_job, preview=True)
            warm_seconds = time.perf_counter() - started
        warm_peak_mb = memory.peak_mb

        fault_job = Job(project.id, "render", input_fingerprint="m6-resume-benchmark")
        original_run_ff = render.run_ff
        injected = False

        def fail_once_at_mux(args, *positional, **kwargs):
            nonlocal injected
            if not injected and "-map" in args and "1:a:0" in args and "-c" in args and "copy" in args:
                injected = True
                raise OSError("simulated interruption at final mux")
            return original_run_ff(args, *positional, **kwargs)

        render.run_ff = fail_once_at_mux
        started = time.perf_counter()
        try:
            render.render(project, fault_job, preview=True)
        except OSError as exc:
            if "simulated interruption" not in str(exc):
                raise
        else:
            raise RuntimeError("Fault injection did not reach final mux")
        interrupted_seconds = time.perf_counter() - started
        render.run_ff = original_run_ff

        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            resumed_result = render.render(project, fault_job, preview=True)
            resume_seconds = time.perf_counter() - started
        resume_peak_mb = memory.peak_mb
        if resumed_result["clip_cache_hits"] < 1 or resumed_result["clip_cache_misses"]:
            raise RuntimeError("Resume did not reuse the completed clip checkpoint")
        outputs = [store.project_dir(project.id) / "exports" / job.id / "preview.mp4"
                   for job in (cold_job, warm_job, fault_job)]
        output_hashes = [media.file_sha256(path) for path in outputs]
        if len(set(output_hashes)) != 1:
            raise RuntimeError("Cold/warm/resume produced different native MP4 bytes")

        measurements = {
            "generate_60s_input_seconds": round(generate_seconds, 2),
            "generate_peak_process_mb": generate_peak_mb,
            "analyze_seconds": round(analyze_seconds, 2),
            "analyze_peak_process_mb": analyze_peak_mb,
            "detected_scenes": len(asset.scenes),
            "cold_render_seconds": round(cold_seconds, 2),
            "cold_render_peak_process_mb": cold_peak_mb,
            "cold_render_output_bytes": cold_result["size"],
            "cold_encoder": cold_result["encoder"],
            "cold_audio_encoder": cold_result["audio_encoder"],
            "warm_render_seconds": round(warm_seconds, 2),
            "warm_render_peak_process_mb": warm_peak_mb,
            "warm_clip_cache_hits": warm_result["clip_cache_hits"],
            "warm_clip_cache_misses": warm_result["clip_cache_misses"],
            "faulted_attempt_seconds": round(interrupted_seconds, 2),
            "mux_resume_seconds": round(resume_seconds, 2),
            "resume_clip_cache_hits": resumed_result["clip_cache_hits"],
            "resume_clip_cache_misses": resumed_result["clip_cache_misses"],
            "resume_peak_process_mb": resume_peak_mb,
            "export_tree_bytes_after_cleanup": _tree_bytes(store.project_dir(project.id) / "exports"),
        }

        api_measurements = _api_latency()
        ffmpeg_line = subprocess.run(
            [media.ffmpeg(), "-version"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.splitlines()[0]
        ffmpeg_version = re.search(r"ffmpeg version\s+([^\s]+)", ffmpeg_line, re.I)
        report = {
            "captured_on": platform.system(),
            "python": platform.python_version(),
            "node": subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5).stdout.strip(),
            "ffmpeg": ffmpeg_version.group(1) if ffmpeg_version else "unknown",
            "aac_encoder": media.audio_encoder(),
            "render_version": render.RENDER_VERSION,
            "fixture_sha256": media.file_sha256(video_path),
            "ffmpeg_binary_sha256": media.file_sha256(media.ffmpeg()),
            "output_sha256": dict(zip(("cold", "warm", "resumed"), output_hashes)),
            "outputs_identical": True,
            "workload": "Synthetic testsrc2, 240x426, 15 fps, 60 seconds; same 5-second portrait preview cold/warm/resume; 110 queued jobs plus a native realtime 720p x264 encode during every API sample.",
            "all_media_and_project_data_in_temporary_directory": True,
            "measurements": measurements,
            "api_latency": api_measurements,
            "limitations": [
                "Single developer workstation run; not a clean Windows installation or a hardware fleet benchmark.",
                "No real provider requests, Vietnamese voice generation, or H.265 path was measured.",
                "M6 fault injection stops before the final mux process starts; partial files are verified by automated acceptance tests.",
            ],
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Sanitized JSON report outside the repo")
    args = parser.parse_args()
    print(json.dumps({"report": str(args.output.resolve()), **benchmark(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
