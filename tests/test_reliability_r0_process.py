"""Windows crash regression using only test-owned process handles and TEMP."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


pytestmark = [pytest.mark.reliability_r0, pytest.mark.skipif(os.name != "nt", reason="F3 requires Windows process ownership")]


def test_f3_ffmpeg_exits_when_backend_owner_is_killed(tmp_path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.TerminateProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    pid_file = tmp_path / "ffmpeg-pid.json"
    code = f'''
import json, os
from pathlib import Path
from backend import media
original = media.subprocess.Popen
def tracked(*args, **kwargs):
    process = original(*args, **kwargs)
    Path({str(pid_file)!r}).write_text(json.dumps({{"pid": process.pid, "owner_pid": os.getpid()}}), encoding="utf-8")
    return process
media.subprocess.Popen = tracked
media.run_ff(["-re", "-f", "lavfi", "-i", "color=c=red:size=64x64:rate=1", "-t", "30", "-f", "null", "-"], timeout=35)
'''
    handle = owner_handle = None
    env = dict(os.environ, CLIPFORGE_DATA=str(tmp_path / "owner-data"))
    with (tmp_path / "owner-stderr.txt").open("wb") as stderr:
        owner = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, stdout=subprocess.DEVNULL, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 10
            while not pid_file.exists() and owner.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            if not pid_file.exists():
                raise RuntimeError("F3 setup could not start FFmpeg; see owner-stderr.txt")
            identity = json.loads(pid_file.read_text(encoding="utf-8"))
            pid = identity["pid"]
            # Windows venv's launcher has a different PID from its actual
            # Python backend. Kill and wait on the process doing the work.
            owner_handle = kernel.OpenProcess(0x100000 | 0x0001, False, identity["owner_pid"])
            if not owner_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            # Retain a handle to this exact process through the assertion and
            # cleanup. Never kill by process name or a potentially reused PID.
            handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x0001, False, pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel.WaitForSingleObject(handle, 200) != 258:
                raise RuntimeError("F3 setup FFmpeg exited before owner termination")
            if not kernel.TerminateProcess(owner_handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel.WaitForSingleObject(owner_handle, 5000) != 0:
                raise RuntimeError("Test-owned backend did not exit")
            owner.wait(timeout=5)
            outcome = kernel.WaitForSingleObject(handle, 3000)
            if outcome == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            print(json.dumps({"finding": "F3", "owner_exited": True, "ffmpeg_still_running": outcome == 258}))
            assert outcome == 0, "F3: FFmpeg is still running after its backend owner exited"
        finally:
            if owner_handle:
                if kernel.WaitForSingleObject(owner_handle, 0) == 258:
                    kernel.TerminateProcess(owner_handle, 1)
                    kernel.WaitForSingleObject(owner_handle, 5000)
                kernel.CloseHandle(owner_handle)
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)
            if handle:
                try:
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        if not kernel.TerminateProcess(handle, 1):
                            raise ctypes.WinError(ctypes.get_last_error())
                        if kernel.WaitForSingleObject(handle, 5000) != 0:
                            raise RuntimeError("Test-owned FFmpeg did not exit during cleanup")
                finally:
                    kernel.CloseHandle(handle)
