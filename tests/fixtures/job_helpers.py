"""Helpers for awaiting jobs admitted by the asynchronous resource dispatcher."""

from __future__ import annotations

import time


def wait_for_job(job, timeout: float = 15):
    deadline = time.monotonic() + timeout
    terminal = {"done", "error", "cancelled", "interrupted", "blocked"}
    while time.monotonic() < deadline:
        future = job._future
        if future is not None:
            future.result(timeout=max(0.01, deadline - time.monotonic()))
            break
        if job.status in terminal:
            break
        time.sleep(0.01)
    if job.status not in terminal:
        raise TimeoutError(f"Job {job.id} did not finish within {timeout} seconds")
    return job
