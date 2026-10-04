"""Build a fresh UI, package it and verify the ZIP in a new temporary venv."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.package_release import ARCHIVE_ROOT, build_release


def verify(output, archive, *, package=True, fixture_ram_gib=None):
    import httpx
    from PIL import Image

    output.mkdir(parents=True, exist_ok=True)
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": [],
              "clean_venv": True, "clean_windows_vm": False, "paid_providers": False,
              "archive": str(archive), "temporary_data_only": True}
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", NO_COLOR="1")

    def run(name, command, cwd, env=environment):
        started = time.monotonic()
        with (output / f"{name}.txt").open("wb") as stream:
            completed = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       timeout=600, check=False)
        result["steps"].append({"name": name, "exit_code": completed.returncode,
                                "seconds": round(time.monotonic() - started, 3)})
        (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"{name} failed; see its saved log")

    with tempfile.TemporaryDirectory(prefix="ClipForge R6 tiếng Việt ") as temporary:
        workspace = Path(temporary)
        if package:
            node = shutil.which("node")
            if not node:
                raise RuntimeError("Node is required to build the release UI")
            dist = workspace / "built-ui"
            run("frontend-build", [node, "node_modules/vite/bin/vite.js", "build", "--outDir", str(dist),
                                   "--emptyOutDir"], ROOT / "frontend")
            archive, digest = build_release(archive, frontend_dist=dist)
        else:
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        result["sha256"] = digest
        extracted = workspace / "Bản phát hành có khoảng trắng"
        extracted.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                target = (extracted / entry.filename).resolve()
                if not target.is_relative_to(extracted.resolve()):
                    raise RuntimeError("Archive entry escapes its test directory")
                if not entry.filename.startswith(ARCHIVE_ROOT + "/"):
                    raise RuntimeError("Archive has an unexpected root")
            manifest = zipped.read(ARCHIVE_ROOT + "/RELEASE-MANIFEST.sha256").decode()
            records = manifest.splitlines()
            for record in records:
                expected, name = record.split("  ", 1)
                if hashlib.sha256(zipped.read(ARCHIVE_ROOT + "/" + name)).hexdigest() != expected:
                    raise RuntimeError("Release manifest does not match payload")
            zipped.extractall(extracted)
        release = extracted / ARCHIVE_ROOT
        result["manifest_entries_verified"] = len(records)
        result["unicode_and_space_path"] = True
        env = {**environment, "CLIPFORGE_DATA": str(workspace / "test-data")}
        run("clean-setup", [sys.executable, "launch.py", "--setup-only"], release, env)
        python = release / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run("pip-check", [str(python), "-m", "pip", "check"], release, env)
        packages = json.loads(subprocess.check_output([str(python), "-m", "pip", "list", "--format=json"], cwd=release, env=env))
        installed = {p["name"].casefold(): p["version"] for p in packages}
        locked_count = 0
        for line in (release / "requirements-lock.txt").read_text().splitlines():
            if "==" in line and not line.startswith("#"):
                name, version = line.strip().split("==")
                if installed.get(name.casefold()) != version:
                    raise RuntimeError("Clean setup did not install the exact dependency lock")
                locked_count += 1
        result["locked_packages_verified"] = locked_count
        result["psutil"] = installed["psutil"]
        result["actual_available_memory_bytes"] = int(subprocess.check_output(
            [str(python), "-c", "import psutil; print(psutil.virtual_memory().available)"], cwd=release, env=env))
        result["ram_telemetry_mocked_for_http_fixture"] = fixture_ram_gib is not None
        result["fixture_ram_gib"] = fixture_ram_gib
        bundled_ffmpeg = subprocess.check_output([str(python), "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"], cwd=release, env=env, text=True, encoding="utf-8").strip()
        env["FFMPEG_BINARY"] = bundled_ffmpeg
        result["bundled_ffmpeg_sha256"] = hashlib.sha256(Path(bundled_ffmpeg).read_bytes()).hexdigest()
        result["ffmpeg"] = subprocess.check_output([bundled_ffmpeg, "-version"], text=True).splitlines()[0]
        run("packaged-regressions", [str(python), "-m", "pytest", "-q", "tests/test_reliability_r5.py",
            "tests/test_reliability_r6.py", "-k", "not native_fault or (rename and after and resume)",
            f"--junitxml={output / 'packaged-regressions.xml'}"], release, env)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"

        def start_server(log):
            command = [str(python), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)]
            if fixture_ram_gib is not None:
                # Test-only bootstrap; never written into the release backend.
                script = f"from backend import jobs; jobs._available_memory_bytes=lambda:{fixture_ram_gib}*1024**3; import uvicorn; uvicorn.run('backend.main:app',host='127.0.0.1',port={port})"
                command = [str(python), "-c", script]
            server = subprocess.Popen(command, cwd=release, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Packaged backend exited before ready")
                try:
                    if httpx.get(url + "/api/health", timeout=1).status_code == 200:
                        return server
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            server.kill()
            server.wait(timeout=10)
            raise RuntimeError("Packaged backend did not become ready")

        with (output / "server.txt").open("wb") as log:
            server = start_server(log)
            try:
                with httpx.Client(base_url=url, timeout=15) as client:
                    def request(method, path, **kwargs):
                        response = client.request(method, path, **kwargs)
                        response.raise_for_status()
                        return response

                    page = request("GET", "/").text
                    ui_asset = re.search(r'src="([^"]+\.js)"', page)[1]
                    ui_bytes = request("GET", ui_asset).content
                    result["served_ui_sha256"] = hashlib.sha256(ui_bytes).hexdigest()
                    assert ui_bytes == (release / "frontend/dist" / ui_asset.lstrip("/")).read_bytes()
                    assert request("GET", "/api/jobs/queue").json()["telemetry_available"]
                    project = request("POST", "/api/projects", json={"name": "R6 clean ZIP"}).json()
                    pid = project["id"]
                    image = io.BytesIO()
                    Image.new("RGB", (64, 64), (50, 130, 190)).save(image, format="PNG")
                    upload = request("POST", f"/api/projects/{pid}/assets", files={"files": ("fixture.png", image.getvalue(), "image/png")}, data={"role": "source"}).json()["job"]

                    def done(jid):
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline:
                            current = request("GET", f"/api/jobs/{jid}").json()
                            if current["status"] == "done":
                                return current
                            if current["status"] in ("error", "blocked", "interrupted"):
                                raise RuntimeError(f"Packaged job failed: {current.get('error', {}).get('code')}")
                            time.sleep(0.1)
                        raise RuntimeError("Packaged job timed out")

                    done(upload["id"])
                    project = request("GET", f"/api/projects/{pid}").json()
                    project.update(resolution="720", aspect="1:1", source_volume=0,
                                   clips=[{"asset_id": project["assets"][0]["id"], "duration": 0.5}])
                    project["template"]["caption"]["enabled"] = False
                    project = request("PUT", f"/api/projects/{pid}", json=project).json()
                    render_job = request("POST", f"/api/projects/{pid}/render", json={"preview": True, "revision": project["revision"]}).json()
                    done(render_job["id"])
                    result["upload_and_render_via_real_http"] = True
                    project = request("GET", f"/api/projects/{pid}").json()
                    assert len(project["exports"]) == 1
                    result["export_filename"] = project["exports"][0]["filename"]
                server.kill()
                server.wait(timeout=10)
                result["real_backend_kills"] = 1
                server = start_server(log)
                with httpx.Client(base_url=url, timeout=15) as client:
                    restored = client.get(f"/api/projects/{pid}").json()
                    assert len(restored["exports"]) == 1
                    assert client.get(f"/api/jobs/{render_job['id']}").json()["status"] == "done"
                result["restart_preserves_committed_result"] = True
            finally:
                if server.poll() is None:
                    server.terminate()
                server.wait(timeout=10)
        result["passed"] = True
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"archive": str(archive), "sha256": digest, "passed": True}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "review/reliability-r6/release")
    parser.add_argument("--archive", type=Path, default=ROOT / "release/ClipForge-1.0.0-R6-source.zip")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--fixture-ram-gib", type=int, choices=range(4, 65), help="Explicit test-only RAM telemetry for tiny HTTP fixtures")
    args = parser.parse_args()
    verify(args.output.resolve(), args.archive.resolve(), package=not args.verify_existing, fixture_ram_gib=args.fixture_ram_gib)


if __name__ == "__main__":
    main()
