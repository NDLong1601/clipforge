"""Own native child trees through backend exit and command cancellation.

Windows 8+ nested Job Objects: the backend owns one lifetime handle; children
inherit it at creation. A suspended child enters a separate command job before
its primary thread resumes, so helpers share cancellation ownership too.
"""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import threading
import time

_OWNER_LOCK = threading.Lock()
_OWNER_HANDLE = None
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = set()
_CREATE_SUSPENDED = 0x4


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IOCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
        ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
        ("priority", wintypes.LONG), ("delta_priority", wintypes.LONG), ("flags", wintypes.DWORD)]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
    }
    for name, (arguments, result) in signatures.items():
        method = getattr(kernel, name)
        method.argtypes, method.restype = arguments, result
    return kernel


def _require(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _new_job(kernel):
    handle = _require(kernel.CreateJobObjectW(None, None))
    try:
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _require(kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        return handle
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def ensure_owner():
    global _OWNER_HANDLE
    if os.name != "nt":
        return
    with _OWNER_LOCK:
        if _OWNER_HANDLE is not None:
            return
        kernel = _kernel()
        handle = _new_job(kernel)
        try:
            _require(kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()))
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        # Never close a job containing this live backend: doing so terminates
        # the owner itself. The OS closes this non-inherited handle on exit.
        _OWNER_HANDLE = handle


def _resume_primary(kernel, pid):
    snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Thread32First(snapshot, ctypes.byref(entry))
        thread_ids = []
        while available:
            if entry.process_id == pid:
                thread_ids.append(entry.thread_id)
            available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        if len(thread_ids) != 1:
            raise OSError("Không xác định được luồng chính của tiến trình đang tạm dừng")
        thread = _require(kernel.OpenThread(0x2, False, thread_ids[0]))
        try:
            if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(thread)
    finally:
        kernel.CloseHandle(snapshot)


class _Command:
    def __init__(self):
        self.process = None
        self.assigned = False
        self.kernel = _kernel() if os.name == "nt" else None
        self.handle = _new_job(self.kernel) if self.kernel else None

    def attach(self, process):
        self.process = process
        if self.kernel:
            _require(self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)))
            self.assigned = True
            _resume_primary(self.kernel, process.pid)

    def stop(self, grace=0.75):
        process = self.process
        if not process:
            return
        if self.kernel and not self.assigned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            return
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
                elif not self.kernel:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=grace)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        # The leader may have exited while helpers remain. Always stop the
        # entire command job/session before relinquishing its ownership.
        if self.kernel:
            _require(self.kernel.TerminateJobObject(self.handle, 1))
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)

    def close(self):
        try:
            self.stop()
        finally:
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
            if self.process:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
                for name in ("_stdout_thread", "_stderr_thread"):
                    reader = getattr(self.process, name, None)
                    if reader:
                        reader.join(timeout=5)
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream:
                        stream.close()
                if self.kernel and self.process.returncode is not None:
                    self.process._handle.Close()


def active_count():
    with _ACTIVE_LOCK:
        return len(_ACTIVE)


@contextmanager
def command(arguments, *, popen_factory=None, **kwargs):
    ensure_owner()
    owned = _Command()
    with _ACTIVE_LOCK:
        _ACTIVE.add(owned)
    try:
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW | _CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        process = (popen_factory or subprocess.Popen)(arguments, **kwargs)
        owned.attach(process)
        yield process
    finally:
        try:
            owned.close()
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(owned)


def run(arguments, *, timeout, check=False, capture_output=False, job=None, **kwargs):
    """Owned replacement for short native capability/TTS commands."""
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with command(arguments, **kwargs) as process:
        deadline = time.monotonic() + timeout
        while True:
            if job:
                job.check()
            remaining = max(0, deadline - time.monotonic())
            try:
                stdout, stderr = process.communicate(timeout=min(0.15, remaining) if job else remaining)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
        if job:
            job.check()
        result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
