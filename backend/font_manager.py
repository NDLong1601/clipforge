from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from PIL import ImageFont

_BUNDLED = Path(__file__).resolve().parent / "fonts"
_KNOWN = {
    "Arial": ("arialbd.ttf", "arial.ttf"),
    "Aptos": ("aptos-bold.ttf", "aptos.ttf"),
    "Calibri": ("calibrib.ttf", "calibri.ttf"),
    "Segoe UI": ("segoeuib.ttf", "segoeui.ttf"),
    "Tahoma": ("tahomabd.ttf", "tahoma.ttf"),
    "Verdana": ("verdanab.ttf", "verdana.ttf"),
    "DejaVu Sans": ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"),
    "Liberation Sans": ("LiberationSans-Bold.ttf", "LiberationSans-Regular.ttf"),
}
_STYLES = re.compile(
    r"([ _-]+)(bolditalic|bold|italic|regular|medium|semibold|light)$", re.I
)


def _font_dirs():
    roots = [_BUNDLED]
    if os.name == "nt":
        roots.extend([Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"])
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    else:
        roots.extend(
            [
                Path("/usr/share/fonts/truetype"),
                Path("/usr/local/share/fonts"),
                Path.home() / ".local" / "share" / "fonts",
            ]
        )
    return [root for root in roots if root.is_dir()]


@lru_cache(maxsize=1)
def _all_files():
    files = []
    for root in _font_dirs():
        try:
            files.extend(root.rglob("*.ttf"))
            files.extend(root.rglob("*.otf"))
        except OSError:
            continue
    return tuple(files)


def _name_from_file(path: Path):
    return _STYLES.sub("", path.stem).replace("_", " ").replace("-", " ").strip()


def _normal(value: str):
    return re.sub(r"[\W_]+", "", value.casefold())


@lru_cache(maxsize=1)
def _font_records():
    records = []
    for path in _all_files():
        try:
            family, style = ImageFont.truetype(str(path), 12).getname()
        except (OSError, ValueError):
            continue
        if family and len(family) <= 80 and re.fullmatch(r"[\w .-]+", family):
            records.append((path, family, style))
    return tuple(records)


def _matches(family):
    wanted = _normal(family)
    records = _font_records()
    exact = [record for record in records if _normal(record[1]) == wanted]
    if exact:
        return exact
    # Preserve projects that saved a filename alias in the old catalog.
    alias = next(
        (
            record
            for record in records
            if wanted in {_normal(record[0].stem), _normal(_name_from_file(record[0]))}
        ),
        None,
    )
    return [record for record in records if record[1] == alias[1]] if alias else []


def _select(records, bold):
    def score(record):
        path, family, style = record
        style = style.casefold()
        is_bold = any(part in style for part in ("bold", "demi", "black", "heavy"))
        aliases = _KNOWN.get(family, ())
        filename = aliases[0 if bold else -1] if aliases else ""
        return (
            is_bold != bold,
            "italic" in style or "oblique" in style,
            path.name.casefold() != filename.casefold(),
            len(path.name),
            path.name.casefold(),
        )

    path, family, _ = min(records, key=score)
    return path, family


def canonical_family(family):
    records = _matches(family)
    return records[0][1] if records else family


def catalog():
    found = {}
    for _, family, _ in _font_records():
        found.setdefault(_normal(family), family)
    result = []
    for preferred in _KNOWN:
        if _normal(preferred) in found:
            result.append(preferred)
    result.extend(
        name
        for key, name in sorted(found.items())
        if key not in {_normal(item) for item in result}
    )
    if not result:
        result = ["Arial"]
    return result


def resolve(family: str, bold: bool = True):
    """Return a local font file, its actual family name, and whether it was found."""
    exact = _matches(family)
    if exact:
        path, actual = _select(exact, bold)
        return path, actual, True
    for fallback in ("Arial", "DejaVu Sans", "Liberation Sans"):
        records = _matches(fallback)
        if records:
            path, actual = _select(records, bold)
            return path, actual, False
    if _font_records():
        path, actual = _select(_font_records(), bold)
        return path, actual, False
    return None, "Arial", False


def available():
    return {"fonts": catalog(), "fallback": resolve("Arial")[1]}
