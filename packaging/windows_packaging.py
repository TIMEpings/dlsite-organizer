"""Build-time isolation and provenance checks for Windows packaging.

This module deliberately has no application imports.  It is used by the
PowerShell build driver, PyInstaller spec files, and packaging-level tests.
"""

from __future__ import annotations

import argparse
import fnmatch
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
NATIVE_ARTIFACT_SUFFIXES = frozenset({".dll", ".exe", ".pyd"})
FORBIDDEN_ARTIFACT_COMPONENTS = frozenset({"codex", "codex-runtimes", "poppler"})

# These are the runtime distributions named in THIRD_PARTY_NOTICES.md.  The
# version part is intentionally matched separately because the canonical
# environment can be refreshed between two verification builds.
REQUIRED_RUNTIME_LICENSE_DISTRIBUTIONS = (
    "pyside6_essentials",
    "shiboken6",
    "sqlalchemy",
    "selectolax",
    "httpx",
    "httpcore",
    "certifi",
    "idna",
    "anyio",
    "h11",
    "pydantic",
    "pydantic_core",
    "greenlet",
    "annotated_types",
    "typing_extensions",
    "typing_inspection",
)


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


def audit_packaged_resources(distribution: Path) -> tuple[Path, ...]:
    """Verify application and runtime license resources in an onedir build."""
    runtime_root = distribution / "_internal"
    roots = (runtime_root, distribution) if runtime_root.is_dir() else (distribution,)

    required_files: list[Path] = []
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        resource = next((root / name for root in roots if (root / name).is_file()), None)
        if resource is None:
            raise FileNotFoundError(f"Packaged resource missing: {name}")
        required_files.append(resource)

    license_roots = tuple(
        root / "licenses"
        for root in (distribution, runtime_root)
        if (root / "licenses").is_dir()
    )
    if not license_roots:
        raise FileNotFoundError("Packaged resource missing: licenses directory")

    resources = list(required_files)
    for distribution_name in REQUIRED_RUNTIME_LICENSE_DISTRIBUTIONS:
        matches = [
            directory
            for license_root in license_roots
            for directory in license_root.iterdir()
            if directory.is_dir()
            and fnmatch.fnmatchcase(
                directory.name.casefold(), f"{distribution_name.casefold()}-*.dist-info"
            )
        ]
        if not matches:
            raise FileNotFoundError(
                f"Packaged third-party license directory missing: {distribution_name}"
            )
        license_files = [
            path
            for directory in matches
            for path in directory.rglob("*")
            if path.is_file()
            and ("license" in path.name.casefold() or "notice" in path.name.casefold())
        ]
        if not license_files:
            raise FileNotFoundError(
                f"Packaged third-party license file missing: {distribution_name}"
            )
        resources.extend(license_files)
    return tuple(resources)


def audit_native_binaries(distribution: Path) -> tuple[Path, ...]:
    """Verify that bundled native files are x64 and contain no foreign runtime."""
    binaries = tuple(
        sorted(
            (
                path
                for path in distribution.rglob("*")
                if path.is_file() and path.suffix.casefold() in NATIVE_ARTIFACT_SUFFIXES
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    )
    if not binaries:
        raise FileNotFoundError("Native PE audit found no executable or native library")

    for path in binaries:
        relative = path.relative_to(distribution)
        components = {part.casefold() for part in relative.parts}
        if components & FORBIDDEN_ARTIFACT_COMPONENTS:
            raise RuntimeError(f"Foreign runtime found in artifact: {relative}")
        header = path.read_bytes()[:4096]
        if len(header) < 64 or header[:2] != b"MZ":
            raise RuntimeError(f"Native PE audit failed: invalid DOS header in {relative}")
        pe_offset = int.from_bytes(header[0x3C:0x40], "little")
        if pe_offset + 6 > len(header) or header[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise RuntimeError(f"Native PE audit failed: invalid PE header in {relative}")
        machine = int.from_bytes(header[pe_offset + 4 : pe_offset + 6], "little")
        if machine != 0x8664:
            raise RuntimeError(
                f"Native PE audit failed: {relative} is not x64 (machine=0x{machine:04x})"
            )
    return binaries


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


def _command_audit_resources(args: argparse.Namespace) -> int:
    try:
        resources = audit_packaged_resources(Path(args.dist))
    except (FileNotFoundError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Packaged resource audit: PASS ({len(resources)} resources)")
    return 0


def _command_audit_native(args: argparse.Namespace) -> int:
    try:
        binaries = audit_native_binaries(Path(args.dist))
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Native PE audit: PASS ({len(binaries)} x64 binaries)")
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

    audit_resources = subparsers.add_parser("audit-resources")
    audit_resources.add_argument("--dist", required=True)
    audit_resources.set_defaults(handler=_command_audit_resources)

    audit_native = subparsers.add_parser("audit-native")
    audit_native.add_argument("--dist", required=True)
    audit_native.set_defaults(handler=_command_audit_native)
    return parser


if __name__ == "__main__":
    parsed_args = _build_parser().parse_args()
    raise SystemExit(parsed_args.handler(parsed_args))
