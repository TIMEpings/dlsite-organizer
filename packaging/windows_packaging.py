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
PRODUCTION_HELPER_FILENAME = "dlsite-shell-helper.exe"
FORBIDDEN_PACKAGE_SUFFIXES = frozenset({
    ".obj",
    ".pdb",
    ".ilk",
    ".lib",
    ".exp",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cmake",
    ".ninja",
})
FORBIDDEN_PACKAGE_COMPONENTS = frozenset({
    "cmakefiles",
    "ctesttestfile.cmake",
    "native-build",
    "pyinstaller-work",
})
FORBIDDEN_PACKAGE_FILENAMES = frozenset({
    "cmakelists.txt",
    "readme",
    "readme.md",
    "cmake.exe",
    "ctest.exe",
    "ninja.exe",
    "shell_helper_com_probe.exe",
    "shell_helper_interop_client.exe",
    "shell_helper_tests.exe",
    "test_launch_stub.exe",
})
REQUIRED_APPLICATION_RESOURCES = (Path("assets") / "branding" / "app_icon.png",)
CERTIFI_BUNDLE_RELATIVE_PATH = Path("certifi") / "cacert.pem"
CERTIFI_BUNDLE_MIN_BYTES = 100_000
CERTIFI_BUNDLE_MAX_BYTES = 1_000_000

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


def audit_certifi_bundle(
    distribution: Path,
    *,
    source_bundle: Path | None = None,
) -> Path:
    """Verify the standard certifi bundle at the frozen package-relative path."""
    bundle = distribution / "_internal" / CERTIFI_BUNDLE_RELATIVE_PATH
    if not bundle.is_file():
        raise FileNotFoundError(f"Packaged certifi bundle missing: {bundle}")

    size = bundle.stat().st_size
    if not CERTIFI_BUNDLE_MIN_BYTES <= size <= CERTIFI_BUNDLE_MAX_BYTES:
        raise RuntimeError(
            "Packaged certifi bundle has an unreasonable size: "
            f"{bundle} ({size} bytes; expected "
            f"{CERTIFI_BUNDLE_MIN_BYTES}..{CERTIFI_BUNDLE_MAX_BYTES})"
        )

    if source_bundle is not None:
        source = Path(source_bundle)
        if not source.is_file():
            raise FileNotFoundError(f"Source certifi bundle missing: {source}")
        if bundle.read_bytes() != source.read_bytes():
            raise RuntimeError(
                "Packaged certifi bundle does not match the current source bundle: "
                f"{bundle} != {source}"
            )
    return bundle


def audit_packaged_resources(distribution: Path) -> tuple[Path, ...]:
    """Verify application branding and runtime license resources in an onedir build."""
    runtime_root = distribution / "_internal"
    roots = (runtime_root, distribution) if runtime_root.is_dir() else (distribution,)

    required_files: list[Path] = []
    for relative_path in REQUIRED_APPLICATION_RESOURCES:
        resource = next(
            (root / relative_path for root in roots if (root / relative_path).is_file()),
            None,
        )
        if resource is None:
            raise FileNotFoundError(f"Packaged resource missing: {relative_path}")
        required_files.append(resource)

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


def audit_packaged_executable_icon(distribution: Path) -> Path:
    """Verify the onedir executable is x64 PE and embeds a group icon resource."""
    executable = distribution / "dlsite-organizer.exe"
    data = executable.read_bytes() if executable.is_file() else b""
    if len(data) < 64 or data[:2] != b"MZ":
        raise RuntimeError(f"Packaged executable icon audit failed: invalid PE {executable}")

    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"Packaged executable icon audit failed: invalid PE {executable}")
    machine = int.from_bytes(data[pe_offset + 4 : pe_offset + 6], "little")
    if machine != 0x8664:
        raise RuntimeError(
            "Packaged executable icon audit failed: "
            f"{executable.name} is not x64 (machine=0x{machine:04x})"
        )

    section_count = int.from_bytes(data[pe_offset + 6 : pe_offset + 8], "little")
    optional_header_size = int.from_bytes(data[pe_offset + 20 : pe_offset + 22], "little")
    optional_header = pe_offset + 24
    if optional_header + optional_header_size > len(data):
        raise RuntimeError("Packaged executable icon audit failed: truncated PE header")
    if int.from_bytes(data[optional_header : optional_header + 2], "little") != 0x20B:
        raise RuntimeError("Packaged executable icon audit failed: expected PE32+ executable")

    resource_directory = optional_header + 112 + (2 * 8)
    if resource_directory + 8 > optional_header + optional_header_size:
        raise RuntimeError("Packaged executable icon audit failed: missing resource directory")
    resource_rva = int.from_bytes(data[resource_directory : resource_directory + 4], "little")
    resource_size = int.from_bytes(data[resource_directory + 4 : resource_directory + 8], "little")
    if not resource_rva or not resource_size:
        raise RuntimeError("Packaged executable icon audit failed: no resource directory")

    section_table = optional_header + optional_header_size
    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        section = section_table + (index * 40)
        if section + 40 > len(data):
            raise RuntimeError("Packaged executable icon audit failed: truncated section table")
        virtual_size = int.from_bytes(data[section + 8 : section + 12], "little")
        virtual_address = int.from_bytes(data[section + 12 : section + 16], "little")
        raw_size = int.from_bytes(data[section + 16 : section + 20], "little")
        raw_pointer = int.from_bytes(data[section + 20 : section + 24], "little")
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer, raw_size))

    resource_offset = _rva_to_file_offset(resource_rva, sections)
    if resource_offset is None or resource_offset + resource_size > len(data):
        raise RuntimeError("Packaged executable icon audit failed: invalid resource directory")
    resource_end = resource_offset + resource_size
    if not _resource_tree_has_group_icon(data, resource_offset, resource_end):
        raise RuntimeError(
            "Packaged executable icon audit failed: RT_GROUP_ICON resource is missing"
        )
    return executable


def _rva_to_file_offset(
    rva: int,
    sections: Iterable[tuple[int, int, int, int]],
) -> int | None:
    for virtual_address, span, raw_pointer, raw_size in sections:
        if virtual_address <= rva < virtual_address + span:
            relative = rva - virtual_address
            if relative < raw_size:
                return raw_pointer + relative
    return None


def _resource_tree_has_group_icon(data: bytes, resource_offset: int, resource_end: int) -> bool:
    """Return whether a PE resource tree contains an RT_GROUP_ICON (type 14)."""
    if resource_offset + 16 > resource_end:
        return False
    named_count = int.from_bytes(data[resource_offset + 12 : resource_offset + 14], "little")
    id_count = int.from_bytes(data[resource_offset + 14 : resource_offset + 16], "little")
    entry_count = named_count + id_count
    entries_offset = resource_offset + 16
    if entries_offset + (entry_count * 8) > resource_end:
        return False

    for index in range(entry_count):
        entry = entries_offset + (index * 8)
        resource_name = int.from_bytes(data[entry : entry + 4], "little")
        resource_location = int.from_bytes(data[entry + 4 : entry + 8], "little")
        if resource_name & 0x80000000 or resource_name != 14:
            continue
        if not resource_location & 0x80000000:
            return False
        group_directory = resource_offset + (resource_location & 0x7FFFFFFF)
        return _resource_tree_has_leaf(data, resource_offset, group_directory, resource_end)
    return False


def _resource_tree_has_leaf(
    data: bytes,
    resource_root: int,
    directory: int,
    resource_end: int,
    *,
    depth: int = 0,
) -> bool:
    if depth > 8 or directory + 16 > resource_end:
        return False
    named_count = int.from_bytes(data[directory + 12 : directory + 14], "little")
    id_count = int.from_bytes(data[directory + 14 : directory + 16], "little")
    entry_count = named_count + id_count
    entries_offset = directory + 16
    if entries_offset + (entry_count * 8) > resource_end:
        return False
    for index in range(entry_count):
        entry = entries_offset + (index * 8)
        resource_location = int.from_bytes(data[entry + 4 : entry + 8], "little")
        if resource_location & 0x80000000:
            child = resource_root + (resource_location & 0x7FFFFFFF)
            if _resource_tree_has_leaf(
                data,
                resource_root,
                child,
                resource_end,
                depth=depth + 1,
            ):
                return True
            continue
        data_entry = resource_root + (resource_location & 0x7FFFFFFF)
        if data_entry + 16 <= resource_end:
            return True
    return False


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


def audit_packaged_helper(distribution: Path) -> Path:
    """Verify the production helper is one root-level x64 GUI PE without foreign runtimes."""
    helpers = tuple(
        sorted(
            (
                path
                for path in distribution.rglob(PRODUCTION_HELPER_FILENAME)
                if path.is_file()
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    )
    if len(helpers) != 1 or helpers[0].parent != distribution:
        locations = ", ".join(str(path.relative_to(distribution)) for path in helpers)
        raise RuntimeError(
            "Packaged helper audit failed: expected exactly one root-level "
            f"{PRODUCTION_HELPER_FILENAME}; found {locations or 'none'}"
        )

    helper = helpers[0]
    _audit_helper_binary(helper)
    return helper


def _audit_helper_binary(helper: Path) -> None:
    """Verify one production helper binary's architecture and PE subsystem."""
    data = helper.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise RuntimeError(f"Packaged helper audit failed: invalid PE {helper}")
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise RuntimeError(f"Packaged helper audit failed: invalid PE header {helper}")
    machine = int.from_bytes(data[pe_offset + 4 : pe_offset + 6], "little")
    if machine != 0x8664:
        raise RuntimeError(
            f"Packaged helper audit failed: {helper.name} is not x64 (machine=0x{machine:04x})"
        )
    optional_header_size = int.from_bytes(data[pe_offset + 20 : pe_offset + 22], "little")
    optional_header = pe_offset + 24
    if optional_header_size < 70 or optional_header + 70 > len(data):
        raise RuntimeError(f"Packaged helper audit failed: invalid optional PE header {helper}")
    subsystem = int.from_bytes(data[optional_header + 68 : optional_header + 70], "little")
    if subsystem != 2:
        raise RuntimeError(
            f"Packaged helper audit failed: {helper.name} is not Windows GUI "
            f"(subsystem=0x{subsystem:04x})"
        )
    lowered = data.lower()
    forbidden_markers = (
        b"python",
        b"qt6",
        b"qt5",
        b"winhttp.dll",
        b"wininet.dll",
        b"ws2_32.dll",
        b"powershell",
        b"cmd.exe",
    )
    for marker in forbidden_markers:
        if marker in lowered:
            raise RuntimeError(
                f"Packaged helper audit failed: forbidden dependency marker {marker!r} in {helper}"
            )


def audit_package_hygiene(distribution: Path) -> tuple[Path, ...]:
    """Reject native build/debug/test artifacts that must stay outside the payload."""
    if not distribution.is_dir():
        raise FileNotFoundError(f"Packaged distribution missing: {distribution}")
    forbidden = tuple(
        sorted(
            (
                path
                for path in distribution.rglob("*")
                if path.is_file()
                and (
                    path.suffix.casefold() in FORBIDDEN_PACKAGE_SUFFIXES
                    or path.name.casefold() in FORBIDDEN_PACKAGE_FILENAMES
                    or any(part.casefold() in FORBIDDEN_PACKAGE_COMPONENTS for part in path.parts)
                )
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    )
    if forbidden:
        rendered = ", ".join(str(path.relative_to(distribution)) for path in forbidden)
        raise RuntimeError(f"Packaged hygiene audit failed: forbidden files {rendered}")
    return forbidden


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


def _command_audit_certifi(args: argparse.Namespace) -> int:
    try:
        bundle = audit_certifi_bundle(
            Path(args.dist),
            source_bundle=Path(args.source) if args.source else None,
        )
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Certifi bundle audit: PASS ({bundle}, {bundle.stat().st_size} bytes)")
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


def _command_audit_icon(args: argparse.Namespace) -> int:
    try:
        executable = audit_packaged_executable_icon(Path(args.dist))
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Packaged executable icon audit: PASS ({executable.name})")
    return 0


def _command_audit_helper(args: argparse.Namespace) -> int:
    try:
        if args.helper:
            helper_path = Path(args.helper)
            _audit_helper_binary(helper_path)
            helper = helper_path
        elif args.dist:
            helper = audit_packaged_helper(Path(args.dist))
        else:
            raise RuntimeError("Helper audit requires --dist or --helper")
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Packaged helper audit: PASS ({helper.name})")
    return 0


def _command_audit_hygiene(args: argparse.Namespace) -> int:
    try:
        audit_package_hygiene(Path(args.dist))
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Packaged hygiene audit: PASS")
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

    audit_certifi = subparsers.add_parser("audit-certifi")
    audit_certifi.add_argument("--dist", required=True)
    audit_certifi.add_argument("--source")
    audit_certifi.set_defaults(handler=_command_audit_certifi)

    audit_resources = subparsers.add_parser("audit-resources")
    audit_resources.add_argument("--dist", required=True)
    audit_resources.set_defaults(handler=_command_audit_resources)

    audit_native = subparsers.add_parser("audit-native")
    audit_native.add_argument("--dist", required=True)
    audit_native.set_defaults(handler=_command_audit_native)

    audit_icon = subparsers.add_parser("audit-icon")
    audit_icon.add_argument("--dist", required=True)
    audit_icon.set_defaults(handler=_command_audit_icon)

    audit_helper = subparsers.add_parser("audit-helper")
    audit_helper.add_argument("--dist")
    audit_helper.add_argument("--helper")
    audit_helper.set_defaults(handler=_command_audit_helper)

    audit_hygiene = subparsers.add_parser("audit-hygiene")
    audit_hygiene.add_argument("--dist", required=True)
    audit_hygiene.set_defaults(handler=_command_audit_hygiene)
    return parser


if __name__ == "__main__":
    parsed_args = _build_parser().parse_args()
    raise SystemExit(parsed_args.handler(parsed_args))
