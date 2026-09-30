"""Measure a small analysis/render workload and write results outside the repo."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend import jobs, media, render, store
from backend.models import Asset, Clip, Project


def _process_tree_working_set() -> int:
    """Return the working set of this process and its live descendants on Windows."""
    if os.name != "nt":
        try:
            import resource

            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
        except (ImportError, AttributeError):
            return 0

    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return 0
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        processes: dict[int, int] = {}
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                processes[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        selected = {os.getpid()}
        while True:
            children = {pid for pid, parent in processes.items() if parent in selected}
            if children <= selected:
                break
            selected |= children
        total = 0
        for pid in selected:
            handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
            if not handle:
                continue
            try:
                counters = MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    total += int(counters.WorkingSetSize)
            finally:
                kernel32.CloseHandle(handle)
        return total
    finally:
        kernel32.CloseHandle(snapshot)


class PeakMemorySampler:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, _process_tree_working_set())
            self._stop.wait(0.1)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=2)
        self.peak = max(self.peak, _process_tree_working_set())

    @property
    def peak_mb(self) -> float:
        return round(self.peak / (1024 * 1024), 1)


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0


def _project_code_fingerprint() -> str:
    code_files = []
    code_files.extend((PROJECT_ROOT / "backend").rglob("*.py"))
    code_files.extend((PROJECT_ROOT / "frontend" / "src").rglob("*"))
    code_files.extend((PROJECT_ROOT / "frontend" / "public").rglob("*"))
    code_files.extend(
        PROJECT_ROOT / "frontend" / name
        for name in ("index.html", "package.json", "package-lock.json", "vite.config.js")
    )
    code_files.extend(
        PROJECT_ROOT / name
        for name in ("launch.py", "requirements-lock.txt", "requirements.txt")
    )
    digest = hashlib.sha256()
    for path in sorted(path for path in code_files if path.is_file()):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _physical_memory_gb() -> float | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    function = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    function.argtypes = [ctypes.POINTER(MemoryStatusEx)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(status)):
        return None
    return round(status.ullTotalPhys / (1024**3), 1)


def _versions() -> dict:
    ffmpeg_path = media.ffmpeg()
    ffmpeg_result = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, check=True)
    node_path = shutil.which("node.exe" if os.name == "nt" else "node")
    npm_path = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    node_version = (
        subprocess.run([node_path, "--version"], capture_output=True, text=True, check=True).stdout.strip()
        if node_path else "not installed"
    )
    npm_command = (
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", npm_path, "--version"]
        if os.name == "nt" and npm_path and npm_path.lower().endswith(".cmd")
        else [npm_path, "--version"] if npm_path else None
    )
    npm_version = (
        subprocess.run(npm_command, capture_output=True, text=True, check=True).stdout.strip()
        if npm_command else "not installed"
    )
    packages = {}
    for package in ("fastapi", "pytest", "av", "opencv-python-headless", "numpy", "imageio-ffmpeg"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = "not installed"
    package_lock = json.loads((PROJECT_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    return {
        "python": platform.python_version(),
        "node": node_version,
        "npm": npm_version,
        "ffmpeg_path": ffmpeg_path,
        "ffmpeg": ffmpeg_result.stdout.splitlines()[0],
        "lock_hashes_sha256": {
            "requirements-lock.txt": hashlib.sha256((PROJECT_ROOT / "requirements-lock.txt").read_bytes()).hexdigest(),
            "frontend/package-lock.json": hashlib.sha256(
                (PROJECT_ROOT / "frontend" / "package-lock.json").read_bytes()
            ).hexdigest(),
        },
        "frontend_lock_versions": {
            name: package_lock["packages"][f"node_modules/{name}"]["version"]
            for name in ("react", "vite", "@vitejs/plugin-react")
        },
        "packages": packages,
    }


def benchmark(output_path: Path) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clipforge-m0-") as temp_name:
        temp_root = Path(temp_name)
        data_root = temp_root / "data"
        store.ROOT = data_root
        jobs.ROOT = data_root

        project = Project(
            name="M0 60 giây 15 fps",
            target_duration=5,
            resolution="720",
            aspect="9:16",
        )
        project.template.caption.enabled = False
        asset_dir = store.project_dir(project.id) / "assets"
        asset_dir.mkdir(parents=True)
        video_path = asset_dir / "stress_60s.mp4"
        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            media.run_ff(
                [
                    "-f", "lavfi", "-i", "testsrc2=size=240x426:rate=15",
                    "-t", "60", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "30", "-pix_fmt", "yuv420p", video_path,
                ]
            )
            generation_seconds = round(time.perf_counter() - started, 2)
        generation_memory_mb = memory.peak_mb
        asset = Asset(
            name="Stress tổng hợp 60 giây",
            filename=video_path.name,
            role="source",
            **media.probe(video_path),
        )
        project.assets.append(asset)
        project = store.save(project)

        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            media.analyze(project.id, asset)
            analysis_seconds = round(time.perf_counter() - started, 2)
        analysis_memory_mb = memory.peak_mb
        project.assets[0] = asset
        project.clips = [
            Clip(
                asset_id=asset.id,
                scene_id=asset.scenes[0].id,
                source_start=asset.scenes[0].start,
                duration=5,
            )
        ]
        project = store.save(project)

        with PeakMemorySampler() as memory:
            started = time.perf_counter()
            result = render.render(project, jobs.Job(project.id, "m0-baseline-render"), preview=True)
            render_seconds = round(time.perf_counter() - started, 2)
        render_memory_mb = memory.peak_mb
        project_dir = store.project_dir(project.id)
        thumb_dir = project_dir / "thumbs"
        export_dir = project_dir / "exports"

        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=PROJECT_ROOT, capture_output=True, text=True
        ).stdout.strip()
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True
        ).stdout.strip()
        project_relative_to_git_root = ""
        tracked_by_parent_git = False
        if git_root:
            project_relative_to_git_root = Path(os.path.relpath(PROJECT_ROOT, git_root)).as_posix()
            tracked_by_parent_git = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", f"{project_relative_to_git_root}/README.md"],
                cwd=git_root,
                capture_output=True,
                text=True,
            ).returncode == 0
        report = {
            "source_version": {
                "tracked_by_enclosing_git": tracked_by_parent_git,
                "enclosing_git_root": git_root,
                "enclosing_git_head_not_clipforge_revision": git_head,
                "project_path_from_enclosing_git_root": project_relative_to_git_root,
                "production_source_sha256": _project_code_fingerprint(),
            },
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
            "physical_memory_gb": _physical_memory_gb(),
            "memory_method": "Windows sampled process-tree working set (Python + live FFmpeg children)",
            "versions": _versions(),
            "workload": {
                "synthetic_input": "testsrc2, 240x426, 15 fps, 60 seconds, H.264 ultrafast CRF 30",
                "analysis": "backend.media.analyze default settings",
                "render": "one 5-second portrait preview, 720-wide, H.264/AAC",
                "all_project_data_and_renders_in_temp_directory": True,
            },
            "measurements": {
                "generate_input_seconds": generation_seconds,
                "generate_peak_process_tree_mb": generation_memory_mb,
                "analyze_seconds": analysis_seconds,
                "analyze_peak_process_tree_mb": analysis_memory_mb,
                "detected_scene_count": len(asset.scenes),
                "thumbnail_count": len(list(thumb_dir.glob("*.jpg"))) if thumb_dir.exists() else 0,
                "thumbnail_bytes": _tree_bytes(thumb_dir),
                "cache_bytes": _tree_bytes(project_dir / "cache"),
                "render_seconds": render_seconds,
                "render_peak_process_tree_mb": render_memory_mb,
                "render_output_bytes": result["size"],
                "export_tree_bytes_after_cleanup": _tree_bytes(export_dir),
            },
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path outside the repository")
    args = parser.parse_args()
    report = benchmark(args.output.resolve())
    print(json.dumps({"report": str(args.output.resolve()), **report["measurements"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
