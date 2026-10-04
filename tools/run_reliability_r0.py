"""Persist reliability suites, remaining raw red evidence and lint/TEMP build.

Run with the project interpreter: .venv/Scripts/python.exe tools/run_reliability_r0.py
Exit 0 means the preparation baseline is valid, not that F1-F9 are fixed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


REPO = Path(__file__).resolve().parents[1]
BACKEND_REDS = {}
FRONTEND_REDS = {}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def junit_results(path):
    counts = Counter()
    findings = Counter()
    unexpected = []
    for case in ET.parse(path).iter("testcase"):
        failure, error, skipped = case.find("failure"), case.find("error"), case.find("skipped")
        name = case.attrib.get("name", "")
        status = "error" if error is not None else "failed" if failure is not None else "skipped" if skipped is not None else "passed"
        counts[status] += 1
        if failure is not None:
            match = re.match(r"test_(f[1-9])_", name)
            body = "".join(failure.itertext())
            if match and "AssertionError" in body and match[1].upper() in body:
                findings[match[1].upper()] += 1
            else:
                unexpected.append(name)
        if error is not None:
            unexpected.append(name)
    return {"counts": dict(counts), "red_findings": dict(findings), "unexpected_failures": unexpected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Artifact directory; default review/reliability-<milestone>/latest")
    parser.add_argument("--regressions-only", action="store_true", help="Run milestone/fixture regressions only; omit full suites, lint and build")
    parser.add_argument("--milestone", choices=("R0", "R1", "R2", "R3", "R4", "R5", "R6"), default="R6", help="Milestone label for current source verification")
    args = parser.parse_args()
    out = (args.output or REPO / "review" / f"reliability-{args.milestone.lower()}" / "latest").resolve()
    out.mkdir(parents=True, exist_ok=True)
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        parser.error("Node and npm must be available")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", NO_COLOR="1")
    env.pop("CLIPFORGE_R0_STRICT", None)
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"{args.milestone} verification; remaining known red findings remain release blockers",
        "milestone": args.milestone,
        "regressions_only": args.regressions_only,
        "environment": {"os": platform.platform(), "python": sys.version, "interpreter": sys.executable,
            "node": subprocess.check_output([node, "--version"], text=True).strip(),
            "psutil_installed": importlib.util.find_spec("psutil") is not None},
        "steps": [],
    }
    summary["environment"]["packages"] = {}
    for name in ("pytest", "fastapi", "av", "imageio-ffmpeg", "psutil"):
        try:
            summary["environment"]["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            summary["environment"]["packages"][name] = None
    import imageio_ffmpeg
    ffmpeg = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    summary["environment"]["ffmpeg"] = subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0]
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO).decode("utf-8").split("\0")
    write_json(out / "run-source.json", {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in sorted(set(filter(None, paths))) if (REPO / name).is_file()})

    def run(name, command, *, cwd=REPO, expected=0, extra_env=None, timeout=1500):
        print(f"START {name}", flush=True)
        started = time.monotonic()
        with (out / f"{name}.txt").open("wb") as log:
            try:
                result = subprocess.run(command, cwd=cwd, env={**env, **(extra_env or {})}, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                log.write(b"\nReliability runner: command timed out\n")
                code = -1
        step = {"name": name, "command": list(map(str, command)), "exit_code": code,
            "expected_exit_code": expected, "seconds": round(time.monotonic() - started, 3),
            "ok": code == expected, "log": f"{name}.txt"}
        summary["steps"].append(step)
        write_json(out / "summary.json", summary)
        print(f"END {name}: exit={code}, expected={expected}, {step['seconds']}s", flush=True)
        return step

    milestones = ["R1", "R2", "R3", "R4", "R5", "R6"]
    included = milestones[:milestones.index(args.milestone) + 1] if args.milestone in milestones else []
    backend_files = ["tests/test_job_runtime_fixture.py", "tests/test_reliability_r0.py", "tests/test_reliability_r0_process.py",
        *(f"tests/test_reliability_{milestone.lower()}.py" for milestone in included)] if args.regressions_only else []
    run("backend-tests", [sys.executable, "-m", "pytest", "-q", *backend_files, f"--junitxml={out / 'backend-tests.xml'}"])
    run("backend-red", [sys.executable, "-m", "pytest", "-q", "tests/test_reliability_r0.py", "tests/test_reliability_r0_process.py", "--runxfail", f"--junitxml={out / 'backend-red.xml'}"], expected=0)
    frontend_files = ["tests/reliabilityR0.test.cjs", *(f"tests/reliability{milestone}.test.cjs" for milestone in included if milestone not in {"R1", "R6"})] if args.regressions_only else []
    run("frontend-tests", [node, "--test", "--test-reporter=tap", *frontend_files], cwd=REPO / "frontend", timeout=120)
    run("frontend-red", [node, "--test", "--test-reporter=tap", "tests/reliabilityR0.test.cjs"], cwd=REPO / "frontend", expected=0, extra_env={"CLIPFORGE_R0_STRICT": "1"}, timeout=120)
    if not args.regressions_only:
        run("frontend-lint", [npm, "run", "lint"], cwd=REPO / "frontend", timeout=120)
        with tempfile.TemporaryDirectory(prefix="clipforge-r0-build-") as build_root:
            run("frontend-build", [node, "node_modules/vite/bin/vite.js", "build", "--outDir", str(Path(build_root) / "dist"), "--emptyOutDir"], cwd=REPO / "frontend", timeout=120)
        if args.milestone in {"R4", "R5", "R6"}:
            run("browser-e2e", [sys.executable, "tools/run_reliability_r4_browser.py", "--output", str(out / "browser")], timeout=700)

    summary["backend"] = junit_results(out / "backend-tests.xml")
    summary["backend_red"] = junit_results(out / "backend-red.xml")
    frontend_raw = (out / "frontend-red.txt").read_text(encoding="utf-8")
    frontend_findings = Counter(re.findall(r"^not ok \d+ - (F[1-9]):", frontend_raw, re.M))
    summary["frontend_red"] = {"red_findings": dict(frontend_findings)}
    expected_backend = dict(BACKEND_REDS)
    if os.name != "nt":
        expected_backend.pop("F3", None)
    expected_backend_total = sum(expected_backend.values())
    summary["red_baseline_valid"] = (
        summary["backend_red"]["red_findings"] == expected_backend
        and not summary["backend_red"]["unexpected_failures"]
        and summary["backend_red"]["counts"].get("failed", 0) == expected_backend_total
        and dict(frontend_findings) == FRONTEND_REDS
        and len(re.findall(r"^not ok \d+ - ", frontend_raw, re.M)) == sum(FRONTEND_REDS.values())
    )
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["baseline_valid"] = all(step["ok"] for step in summary["steps"]) and summary["red_baseline_valid"]
    summary["release_ready"] = False
    write_json(out / "summary.json", summary)
    print(json.dumps({"output": str(out), "baseline_valid": summary["baseline_valid"], "red_baseline_valid": summary["red_baseline_valid"], "release_ready": False}), flush=True)
    return 0 if summary["baseline_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
