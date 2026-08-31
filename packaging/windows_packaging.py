"""Build-time isolation and provenance checks for Windows packaging.

This module deliberately has no application imports.  It is used by the
PowerShell build driver, PyInstaller spec files, and packaging-level tests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

FOREIGN_PATH_COMPONENTS = frozenset(
    {
        ".codex",
        "codex",
        "codex-runtimes",
        "poppler",
        "qt-sdk",
        "qt_sdk",
    }
)
TEMPORARY_PATH_COMPONENTS = frozenset({".cache", ".tmp", "tmp", "temp", "temporary"})


class ForeignIcuContaminationError(RuntimeError):
    """Raised when a bundled ICU DLL came from an unrelated runtime."""


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[\\/]+", value.casefold()) if part)


def _normalise_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").casefold()


def is_foreign_provider_path(value: str) -> bool:
    """Return whether a path looks like an unrelated binary provider.

    The check intentionally uses path components rather than a user-specific
    absolute path.  PySide6's own ``site-packages/PySide6/Qt/bin`` remains
    trusted while a separately installed Qt SDK's ``Qt/.../bin`` is removed.
    """

    parts = _path_parts(value)
    if any(component in FOREIGN_PATH_COMPONENTS for component in parts):
        return True

    if "qt" in parts and "bin" in parts and "pyside6" not in parts:
        return True

    if any(component in TEMPORARY_PATH_COMPONENTS for component in parts):
        runtime_markers = {"runtime", "runtimes", "tool", "tools", "dependencies"}
        if any(component in runtime_markers or "runtime" in component for component in parts):
            return True

    return False


def is_icu_binary(name: str) -> bool:
    """Return whether a filename is an ICU Windows DLL."""

    lowered = Path(name).name.casefold()
    return lowered.startswith("icu") and lowered.endswith(".dll")


def isolated_build_path(
    original_path: str,
    *,
    python_executable: str,
    system_root: str | None = None,
) -> tuple[str, list[str], list[str]]:
    """Filter build PATH while retaining ordinary and required entries.

    The returned tuple is ``(path, kept_entries, removed_entries)``.  The
    canonical Python directory is added even when it was not already on PATH;
    Python and PyInstaller are invoked by absolute path, so no shell lookup is
    needed.  All non-foreign ordinary entries are retained for build tools
    such as Windows SDK utilities.
    """

    entries = [entry for entry in original_path.split(os.pathsep) if entry]
    required = [str(Path(python_executable).resolve().parent)]
    if system_root:
        root = Path(system_root)
        required.extend(
            [
                str(root),
                str(root / "System32"),
                str(root / "System32" / "Wbem"),
                str(root / "System32" / "WindowsPowerShell" / "v1.0"),
                str(root / "System32" / "OpenSSH"),
            ]
        )

    kept: list[str] = []
    removed: list[str] = []

    def add_unique(candidate: str) -> None:
        if not candidate:
            return
        key = _normalise_path(candidate)
        if all(_normalise_path(existing) != key for existing in kept):
            kept.append(candidate)

    for entry in entries:
        if is_foreign_provider_path(entry):
            removed.append(entry)
        else:
            add_unique(entry)
    for entry in required:
        add_unique(entry)

    return os.pathsep.join(kept), kept, removed


def _destination_key(value: str) -> str:
    return value.replace("\\", "/").lstrip("./").casefold()


def write_provenance_manifest(path: Path, entries: Iterable[dict[str, str]]) -> None:
    """Write the ICU provenance observed by a PyInstaller Analysis."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": 1,
        "entries": sorted(entries, key=lambda entry: _destination_key(entry["destination"])),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def audit_distribution(distribution: Path, provenance_path: Path) -> list[Path]:
    """Audit bundled ICU files against the PyInstaller source manifest."""

    if not provenance_path.is_file():
        raise ForeignIcuContaminationError(
            "Foreign ICU DLL contamination detected: missing PyInstaller provenance manifest"
        )

    try:
        payload: dict[str, Any] = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ForeignIcuContaminationError(
            "Foreign ICU DLL contamination detected: unreadable provenance manifest"
        ) from exc

    manifest_entries = payload.get("entries")
    if not isinstance(manifest_entries, list):
        raise ForeignIcuContaminationError(
            "Foreign ICU DLL contamination detected: invalid provenance manifest"
        )

    by_destination: dict[str, list[dict[str, Any]]] = {}
    for entry in manifest_entries:
        if isinstance(entry, dict):
            destination = _destination_key(str(entry.get("destination", "")))
            if destination:
                by_destination.setdefault(destination, []).append(entry)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entries in by_destination.values():
        for entry in entries:
            name = Path(str(entry.get("destination", ""))).name.casefold()
            if name:
                by_name.setdefault(name, []).append(entry)

    bundled_icu = sorted(
        (path for path in distribution.rglob("*") if path.is_file() and is_icu_binary(path.name)),
        key=lambda path: path.as_posix().casefold(),
    )
    for path in bundled_icu:
        relative = _destination_key(path.relative_to(distribution).as_posix())
        candidates = by_destination.get(relative, [])
        if not candidates:
            candidates = by_name.get(path.name.casefold(), [])
        if not candidates:
            raise ForeignIcuContaminationError(
                "Foreign ICU DLL contamination detected: "
                f"{path.name} has no recorded PyInstaller source provenance"
            )

        for entry in candidates:
            source = str(entry.get("source", ""))
            if is_foreign_provider_path(source):
                raise ForeignIcuContaminationError(
                    "Foreign ICU DLL contamination detected: "
                    f"{path.name} collected from unrelated path {source}"
                )

    return bundled_icu


def _command_clean_path(args: argparse.Namespace) -> int:
    path, kept, removed = isolated_build_path(
        args.path,
        python_executable=args.python_executable,
        system_root=args.system_root,
    )
    print(json.dumps({"path": path, "kept": kept, "removed": removed}))
    return 0


def _command_audit_dist(args: argparse.Namespace) -> int:
    try:
        bundled_icu = audit_distribution(Path(args.dist), Path(args.provenance))
    except ForeignIcuContaminationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if bundled_icu:
        print("ICU provenance audit: PASS")
        for path in bundled_icu:
            print(f"  {path.relative_to(Path(args.dist))}")
    else:
        print(
            "ICU provenance audit: PASS "
            "(no ICU DLLs bundled; Windows system ICU semantics retained)"
        )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    clean_path = subparsers.add_parser("clean-path")
    clean_path.add_argument("--path", required=True)
    clean_path.add_argument("--python-executable", required=True)
    clean_path.add_argument("--system-root")
    clean_path.set_defaults(handler=_command_clean_path)

    audit_dist = subparsers.add_parser("audit-dist")
    audit_dist.add_argument("--dist", required=True)
    audit_dist.add_argument("--provenance", required=True)
    audit_dist.set_defaults(handler=_command_audit_dist)
    return parser


if __name__ == "__main__":
    parsed_args = _build_parser().parse_args()
    raise SystemExit(parsed_args.handler(parsed_args))
