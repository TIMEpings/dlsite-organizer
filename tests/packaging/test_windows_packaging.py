from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_HELPER_PATH = Path(__file__).parents[2] / "packaging" / "windows_packaging.py"
_SPEC = importlib.util.spec_from_file_location("windows_packaging", _HELPER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
windows_packaging = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(windows_packaging)


def test_isolated_build_path_removes_foreign_providers_and_keeps_build_tools(
    tmp_path: Path,
) -> None:
    fake_foreign_binary_dir = tmp_path / "fake-foreign-runtime" / "poppler" / "Library" / "bin"
    fake_foreign_binary_dir.mkdir(parents=True)
    original = ";".join(
        [
            str(fake_foreign_binary_dir),
            r"C:\Qt\6.8.2\msvc2022_64\bin",
            r"C:\Program Files\Git\cmd",
            r"C:\Windows\System32",
        ]
    )

    isolated, kept, removed = windows_packaging.isolated_build_path(
        original,
        python_executable=r"C:\work\.venv\Scripts\python.exe",
        system_root=r"C:\Windows",
    )

    assert r"C:\Program Files\Git\cmd" in kept
    assert r"C:\Windows\System32" in kept
    assert r"C:\work\.venv\Scripts" in kept
    assert all("poppler" not in value.casefold() for value in kept)
    assert all("\\qt\\" not in value.casefold() for value in kept)
    assert len(removed) == 2
    assert all(value in original for value in removed)
    assert "poppler" not in isolated.casefold()


def test_pyside6_qt_bin_is_not_mistaken_for_a_foreign_qt_sdk() -> None:
    assert not windows_packaging.is_foreign_provider_path(
        r"C:\work\.venv\Lib\site-packages\PySide6\Qt\bin"
    )


def test_audit_distribution_rejects_foreign_icu_source(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    bundled = distribution / "_internal" / "icuuc.dll"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture")
    provenance = tmp_path / "provenance.json"
    windows_packaging.write_provenance_manifest(
        provenance,
        [
            {
                "destination": r"_internal\icuuc.dll",
                "source": r"C:\codex-runtimes\poppler\Library\bin\icuuc.dll",
                "typecode": "BINARY",
            }
        ],
    )

    with pytest.raises(
        windows_packaging.ForeignIcuContaminationError,
        match="Foreign ICU DLL contamination detected",
    ):
        windows_packaging.audit_distribution(distribution, provenance)


def test_audit_distribution_accepts_no_icu_bundle(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"format": 1, "entries": []}), encoding="utf-8")

    assert windows_packaging.audit_distribution(distribution, provenance) == []


def test_audit_packaged_resources_requires_application_and_runtime_licenses(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "dist"
    internal = distribution / "_internal"
    internal.mkdir(parents=True)
    branding = internal / "assets" / "branding"
    branding.mkdir(parents=True)
    (branding / "app_icon.png").write_bytes(b"PNG")
    (internal / "LICENSE").write_text("MIT", encoding="utf-8")
    (internal / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
    license_root = distribution / "licenses"
    for name in windows_packaging.REQUIRED_RUNTIME_LICENSE_DISTRIBUTIONS:
        package_dir = license_root / f"{name}-1.0.dist-info"
        package_dir.mkdir(parents=True)
        (package_dir / "LICENSE").write_text(name, encoding="utf-8")

    resources = windows_packaging.audit_packaged_resources(distribution)

    assert len(resources) == 19
    assert internal / "LICENSE" in resources
    assert license_root / "httpx-1.0.dist-info" / "LICENSE" in resources


def test_audit_packaged_resources_rejects_missing_runtime_license(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    internal = distribution / "_internal"
    internal.mkdir(parents=True)
    branding = internal / "assets" / "branding"
    branding.mkdir(parents=True)
    (branding / "app_icon.png").write_bytes(b"PNG")
    (internal / "LICENSE").write_text("MIT", encoding="utf-8")
    (internal / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
    (distribution / "licenses").mkdir()

    with pytest.raises(FileNotFoundError, match="third-party license directory missing"):
        windows_packaging.audit_packaged_resources(distribution)


def test_audit_packaged_resources_rejects_missing_branding_png(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    internal = distribution / "_internal"
    internal.mkdir(parents=True)
    (internal / "LICENSE").write_text("MIT", encoding="utf-8")
    (internal / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
    license_root = distribution / "licenses"
    for name in windows_packaging.REQUIRED_RUNTIME_LICENSE_DISTRIBUTIONS:
        package_dir = license_root / f"{name}-1.0.dist-info"
        package_dir.mkdir(parents=True)
        (package_dir / "LICENSE").write_text(name, encoding="utf-8")

    with pytest.raises(
        FileNotFoundError,
        match=re.escape(str(Path("assets") / "branding" / "app_icon.png")),
    ):
        windows_packaging.audit_packaged_resources(distribution)


def test_packaging_spec_embeds_ico_and_collects_runtime_png() -> None:
    spec_path = _HELPER_PATH.parents[0] / "dlsite-organizer.spec"
    spec_text = spec_path.read_text(encoding="utf-8")

    assert "_branding_ico" in spec_text
    assert "icon=str(_branding_ico)" in spec_text
    assert "_branding_png" in spec_text
    assert 'str(Path("assets") / "branding")' in spec_text


def test_audit_native_binaries_accepts_x64_pe_and_rejects_other_architecture(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()

    x64 = bytearray(256)
    x64[:2] = b"MZ"
    x64[0x3C:0x40] = (128).to_bytes(4, "little")
    x64[128:132] = b"PE\0\0"
    x64[132:134] = (0x8664).to_bytes(2, "little")
    (distribution / "ok.dll").write_bytes(x64)

    assert windows_packaging.audit_native_binaries(distribution) == (distribution / "ok.dll",)

    x86 = bytearray(x64)
    x86[132:134] = (0x014C).to_bytes(2, "little")
    (distribution / "wrong.pyd").write_bytes(x86)
    with pytest.raises(RuntimeError, match="not x64"):
        windows_packaging.audit_native_binaries(distribution)
