"""Create a source release ZIP with the prebuilt UI and runtime instructions."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
ARCHIVE_ROOT = f"ClipForge-{VERSION}"
ROOT_FILES = (
    "README.md",
    "VALIDATION.md",
    "RELEASE_NOTES.md",
    "THIRD_PARTY.md",
    "IMPLEMENTATION_PLAN.md",
    "launch.py",
    "MO_CLIPFORGE.bat",
    "START.bat",
    "requirements.txt",
    "requirements-lock.txt",
    "requirements-local-ai.txt",
    "pytest.ini",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.js",
    "frontend/biome.json",
)
SOURCE_DIRS = (
    "backend",
    "frontend/src",
    "frontend/public",
    "frontend/dist",
    "frontend/tests",
    "licenses",
    "tests",
    "tools",
)
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "data",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "release",
    "review",
}
EXCLUDED_FILES = {".env", ".clipforge-ready", ".DS_Store"}


def _release_files() -> list[Path]:
    paths = [ROOT / name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            raise FileNotFoundError(f"Release source directory is missing: {directory}")
        for path in base.rglob("*"):
            relative = path.relative_to(ROOT)
            if path.is_symlink() or not path.is_file():
                continue
            if any(part in EXCLUDED_DIRS for part in relative.parts):
                continue
            if path.name in EXCLUDED_FILES or path.suffix in {".pyc", ".pyo", ".log"}:
                continue
            paths.append(path)
    missing = [
        path.relative_to(ROOT).as_posix() for path in paths if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "Required release files are missing: " + ", ".join(missing)
        )
    return sorted(
        set(paths), key=lambda path: path.relative_to(ROOT).as_posix().casefold()
    )


def build_release(output: Path | None = None) -> tuple[Path, str]:
    output = (output or ROOT / "release" / f"ClipForge-{VERSION}-source.zip").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    entries = _release_files()
    records = []
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in entries:
                relative = path.relative_to(ROOT).as_posix()
                archive_name = f"{ARCHIVE_ROOT}/{relative}"
                content = path.read_bytes()
                archive.writestr(archive_name, content)
                records.append(f"{hashlib.sha256(content).hexdigest()}  {relative}")
            archive.writestr(
                f"{ARCHIVE_ROOT}/RELEASE-MANIFEST.sha256",
                "\n".join(records) + "\n",
            )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )
    return output, digest


def main() -> None:
    archive, digest = build_release()
    print(f"Release archive: {archive}")
    print(f"SHA-256: {digest}")
    print(f"Size: {archive.stat().st_size} bytes")


if __name__ == "__main__":
    main()
