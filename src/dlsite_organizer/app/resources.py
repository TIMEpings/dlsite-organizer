"""Locate files shipped with the source tree or a frozen application."""

from __future__ import annotations

import sys
from pathlib import Path


def application_resource_path(relative_path: str | Path) -> Path:
    """Return the path to an application resource.

    PyInstaller's onedir layout places collected data beside the frozen
    Python runtime, while the build script keeps the license directory beside
    the executable.  Keeping those lookup rules here prevents UI code from
    depending on either layout or on a source-checkout working directory.
    """

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Application resources must use a relative child path")

    roots: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(frozen_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[3])

    for root in roots:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return roots[0] / relative
