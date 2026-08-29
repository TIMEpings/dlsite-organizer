"""Filesystem-safe naming rules."""

from __future__ import annotations

import re

_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    flags=re.IGNORECASE,
)


def sanitize_windows_name(value: str, replacement: str = "_") -> str:
    """Return a predictable Windows-safe file or directory name."""
    sanitized = _WINDOWS_ILLEGAL.sub(replacement, value)
    sanitized = sanitized.rstrip(" .")
    if _WINDOWS_RESERVED.fullmatch(sanitized):
        sanitized = f"_{sanitized}"
    return sanitized
