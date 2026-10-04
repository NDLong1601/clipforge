"""Subprocess-only fixture: real backend lifetime, worker and FFmpeg encode."""

import json
import os
from pathlib import Path

from backend import jobs, main, media, store
from backend.models import Project
import uvicorn

# Ownership is the subject of this fixture, independent of host RAM pressure.
jobs._available_memory_bytes = lambda: 8 * 1024**3


class FixtureServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.should_exit:
            return
        port = self.servers[0].sockets[0].getsockname()[1]
        project = store.save(Project(name="R2 native crash"))
        job = jobs.reserve(project.id, "render", snapshot=project.model_dump(), config_fingerprint=main._job_config_fingerprint("render"))
        output = store.project_dir(project.id) / "exports" / job.id / "partial.mp4"
        output.parent.mkdir(parents=True)
        original = media.subprocess.Popen

        def tracked(*args, **kwargs):
            process = original(*args, **kwargs)
            (store.ROOT / "process-identity.json").write_text(json.dumps({
                "owner_pid": os.getpid(), "ffmpeg_pid": process.pid,
                "project_id": project.id, "job_id": job.id,
                "port": port,
                "ram_telemetry_mocked": True,
            }), encoding="utf-8")
            return process

        media.subprocess.Popen = tracked

        def worker(current):
            media.run_ff(["-re", "-f", "lavfi", "-i", "color=c=red:size=64x64:rate=5", "-t", "60", "-c:v", "libx264", output], job=current, timeout=70)
            current.check()
            raise RuntimeError("Crash fixture must be stopped by its test")

        jobs.launch(job, worker)


if __name__ == "__main__":
    FixtureServer(uvicorn.Config(main.app, host="127.0.0.1", port=0, access_log=False)).run()
