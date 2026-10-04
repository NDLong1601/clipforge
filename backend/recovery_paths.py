"""Constrain recovery file operations to their owned, non-linked directories."""

from pathlib import Path
import shutil
import stat


def checked_path(root: Path, path: Path) -> Path:
    root, path = root.absolute(), path.absolute()
    if not path.is_relative_to(root):
        raise ValueError("Đường dẫn phục hồi nằm ngoài thư mục dữ liệu")
    for part in (root, *reversed(path.parents), path):
        if not part.is_relative_to(root):
            continue
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("Đường dẫn phục hồi chứa symlink hoặc junction")
    resolved_root, resolved = root.resolve(), path.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("Đường dẫn phục hồi nằm ngoài thư mục dữ liệu")
    return path


def remove_tree(root: Path, path: Path) -> None:
    path = checked_path(root, path)
    if path == root.absolute():
        raise ValueError("Không được xóa thư mục gốc dữ liệu")
    if path.is_dir():
        # Python rmtree unlinks nested symlinks/junctions without following
        # their targets; the directory and all its ancestors were checked.
        shutil.rmtree(path)


def unlink(root: Path, path: Path) -> None:
    checked_path(root, path).unlink(missing_ok=True)
