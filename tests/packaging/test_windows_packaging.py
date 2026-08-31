from __future__ import annotations

import importlib.util
import json
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
