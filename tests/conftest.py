"""Test-wide data isolation and scheduler lifecycle, before backend imports."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest


# Backend modules create their root at import time, during test collection.
# Never let collection touch the user's real ClipForge data directory.
_previous_data = os.environ.get("CLIPFORGE_DATA")
_collection_data = tempfile.TemporaryDirectory(prefix="clipforge-pytest-collection-")
os.environ["CLIPFORGE_DATA"] = _collection_data.name

from tests.fixtures.job_runtime import isolated_job_runtime  # noqa: E402


@pytest.fixture(autouse=True)
def job_runtime(tmp_path, monkeypatch):
    # Depending on monkeypatch keeps its undo until after the worker join,
    # including for older module-local fixtures that patch ROOT themselves.
    with isolated_job_runtime(tmp_path) as pool:
        yield pool


@pytest.fixture
def r0_runtime(job_runtime, monkeypatch):
    from backend import jobs

    monkeypatch.setattr(jobs, "_ensure_scheduler_locked", lambda: None)
    jobs._SCHEDULER_PAUSED = True
    jobs._save_scheduler_settings()
    return Path(jobs.ROOT)


def pytest_unconfigure(config):
    if _previous_data is None:
        os.environ.pop("CLIPFORGE_DATA", None)
    else:
        os.environ["CLIPFORGE_DATA"] = _previous_data
    _collection_data.cleanup()
