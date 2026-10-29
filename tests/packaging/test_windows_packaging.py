from __future__ import annotations

import importlib.util
import json
import re
import runpy
from importlib import metadata
from pathlib import Path
from zipfile import ZipFile

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


def test_audit_certifi_bundle_requires_package_relative_internal_path(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    internal = distribution / "_internal"
    internal.mkdir(parents=True)
    (internal / "cacert.pem").write_bytes(b"x" * windows_packaging.CERTIFI_BUNDLE_MIN_BYTES)

    with pytest.raises(FileNotFoundError, match="Packaged certifi bundle missing"):
        windows_packaging.audit_certifi_bundle(distribution)


def test_audit_certifi_bundle_checks_size_and_source_bytes(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    bundle = distribution / "_internal" / windows_packaging.CERTIFI_BUNDLE_RELATIVE_PATH
    bundle.parent.mkdir(parents=True)
    source = tmp_path / "source-cacert.pem"
    source.write_bytes(b"certificate bundle" * 10_000)
    bundle.write_bytes(source.read_bytes())

    assert windows_packaging.audit_certifi_bundle(distribution, source_bundle=source) == bundle

    bundle.write_bytes(b"different bundle" * 10_000)
    with pytest.raises(RuntimeError, match="does not match"):
        windows_packaging.audit_certifi_bundle(distribution, source_bundle=source)


@pytest.fixture(autouse=True)
def no_installed_greenlet(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> metadata.Distribution:
        raise metadata.PackageNotFoundError("greenlet")

    monkeypatch.setattr(windows_packaging.metadata, "distribution", missing)


@pytest.fixture
def licensed_distribution(
    tmp_path: Path,
) -> Path:
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
    return distribution


def test_audit_packaged_resources_requires_application_and_runtime_licenses(
    licensed_distribution: Path,
) -> None:
    distribution = licensed_distribution
    internal = distribution / "_internal"
    license_root = distribution / "licenses"

    resources = windows_packaging.audit_packaged_resources(distribution)

    assert len(resources) == 18
    assert internal / "LICENSE" in resources
    assert license_root / "httpx-1.0.dist-info" / "LICENSE" in resources


@pytest.fixture
def installed_greenlet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dist_info = tmp_path / "source" / "greenlet-1.0.dist-info"
    licenses = dist_info / "licenses"
    licenses.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Name: greenlet\nVersion: 1.0\n", encoding="utf-8")
    for name in ("LICENSE", "LICENSE.PSF"):
        (licenses / name).write_text(f"original {name}", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "".join(
            f"greenlet-1.0.dist-info/licenses/{name},,\n" for name in ("LICENSE", "LICENSE.PSF")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        windows_packaging.metadata,
        "distribution",
        lambda _name: metadata.PathDistribution(dist_info),
    )
    return licenses


def test_greenlet_shipped_with_original_licenses_passes(
    licensed_distribution: Path,
    installed_greenlet: Path,
) -> None:
    import shutil

    distribution = licensed_distribution
    (distribution / "_internal" / "greenlet").mkdir()
    (distribution / "_internal" / "greenlet" / "_greenlet.cp312-win_amd64.pyd").write_bytes(
        b"synthetic binary"
    )
    target = distribution / "licenses" / "greenlet-1.0.dist-info"
    shutil.copytree(installed_greenlet, target)
    resources = windows_packaging.audit_packaged_resources(distribution)
    assert target / "LICENSE.PSF" in resources
    assert target / "LICENSE" in resources


@pytest.mark.parametrize("missing", ["directory", "LICENSE", "LICENSE.PSF"])
def test_greenlet_shipped_without_required_license_fails(
    licensed_distribution: Path,
    installed_greenlet: Path,
    missing: str,
) -> None:
    import shutil

    distribution = licensed_distribution
    (distribution / "_internal" / "greenlet").mkdir()
    if missing != "directory":
        target = distribution / "licenses" / "greenlet-1.0.dist-info"
        shutil.copytree(installed_greenlet, target)
        (target / missing).unlink()
    with pytest.raises(FileNotFoundError, match="greenlet"):
        windows_packaging.audit_packaged_resources(distribution)


@pytest.mark.parametrize("changed", ["LICENSE", "LICENSE.PSF"])
def test_greenlet_license_integrity_is_checked(
    licensed_distribution: Path,
    installed_greenlet: Path,
    changed: str,
) -> None:
    import shutil

    target = licensed_distribution / "licenses" / "greenlet-1.0.dist-info"
    shutil.copytree(installed_greenlet, target)
    (target / changed).write_text("altered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match installed source"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


def test_installed_greenlet_requires_license_even_without_collected_code(
    licensed_distribution: Path,
    installed_greenlet: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="greenlet"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


def test_greenlet_wrong_version_license_cannot_substitute(
    licensed_distribution: Path,
    installed_greenlet: Path,
) -> None:
    import shutil

    shutil.copytree(
        installed_greenlet, licensed_distribution / "licenses" / "greenlet-0.9.dist-info"
    )
    with pytest.raises(FileNotFoundError, match=r"greenlet-1\.0"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


def test_greenlet_incomplete_source_metadata_fails_closed(
    licensed_distribution: Path,
    installed_greenlet: Path,
) -> None:
    (installed_greenlet.parent / "RECORD").write_text(
        "greenlet-1.0.dist-info/licenses/LICENSE,,\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="metadata is incomplete"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


@pytest.mark.parametrize("inventory", ["package", "binary", "metadata", "zip", "pyz", "exe"])
def test_greenlet_inventory_cannot_bypass_absent_installed_metadata(
    licensed_distribution: Path,
    tmp_path: Path,
    inventory: str,
) -> None:
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter

    internal = licensed_distribution / "_internal"
    if inventory == "package":
        (internal / "greenlet").mkdir()
    elif inventory == "binary":
        (internal / "_greenlet.cp312-win_amd64.pyd").write_bytes(b"binary fixture")
    elif inventory == "metadata":
        (internal / "greenlet-1.0.dist-info").mkdir()
    elif inventory == "zip":
        with ZipFile(internal / "base_library.zip", "w") as archive:
            archive.writestr("greenlet/__init__.pyc", b"fixture")
    else:
        # Synthetic archive tables only: no build or application execution.
        pyz = tmp_path / "modules.pyz" if inventory == "exe" else internal / "modules.pyz"
        ZlibArchiveWriter(str(pyz), [("greenlet", None, "PYMODULE")])
        if inventory == "exe":
            CArchiveWriter(
                str(licensed_distribution / "dlsite-organizer.exe"),
                [("PYZ.pyz", str(pyz), False, "z")],
                "python312.dll",
            )
    with pytest.raises(RuntimeError, match="no installed metadata"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


def test_unreadable_packaged_archive_fails_closed(licensed_distribution: Path) -> None:
    (licensed_distribution / "dlsite-organizer.exe").write_bytes(b"invalid archive")
    with pytest.raises(RuntimeError, match="Cannot inventory"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


def test_other_mandatory_license_still_required(licensed_distribution: Path) -> None:
    (licensed_distribution / "licenses" / "httpx-1.0.dist-info" / "LICENSE").unlink()
    with pytest.raises(FileNotFoundError, match="license file missing: httpx"):
        windows_packaging.audit_packaged_resources(licensed_distribution)


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


def test_packaging_spec_excludes_selectolax_development_sources() -> None:
    spec_path = _HELPER_PATH.parents[0] / "dlsite-organizer.spec"
    spec_text = spec_path.read_text(encoding="utf-8")

    for suffix in ("c", "pxd", "pxi", "pyi", "pyx", "typed"):
        assert f'"**/*.{suffix}"' in spec_text
    assert 'collect_data_files("selectolax", excludes=_selectolax_data_excludes)' in spec_text
    assert 'hookspath=[str(project_root / "packaging" / "hooks")]' in spec_text

    hook_path = _HELPER_PATH.parents[0] / "hooks" / "hook-selectolax.py"
    hook_text = hook_path.read_text(encoding="utf-8")
    assert (
        'datas = collect_data_files("selectolax", excludes=_selectolax_data_excludes)'
        in hook_text
    )

    certifi_hook_path = _HELPER_PATH.parents[0] / "hooks" / "hook-certifi.py"
    certifi_hook_text = certifi_hook_path.read_text(encoding="utf-8")
    assert 'collect_data_files("certifi", includes=["cacert.pem"])' in certifi_hook_text
    hook_namespace = runpy.run_path(str(certifi_hook_path))
    certifi_entries = [
        (Path(source), Path(destination))
        for source, destination in hook_namespace["datas"]
        if Path(source).name == "cacert.pem"
    ]
    assert certifi_entries
    assert all(destination == Path("certifi") for _, destination in certifi_entries)
    assert all(destination != Path(".") for _, destination in certifi_entries)

    smoke_path = _HELPER_PATH.parents[0] / "tls_runtime_smoke.py"
    smoke_text = smoke_path.read_text(encoding="utf-8")
    assert "certifi.where()" in smoke_text
    assert "ssl.create_default_context(cafile=str(bundle))" in smoke_text
    assert "with httpx.Client()" in smoke_text
    for forbidden in ("verify=False", "CERT_NONE", "check_hostname=False", "http://"):
        assert forbidden not in smoke_text


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


def _minimal_x64_pe() -> bytearray:
    data = bytearray(320)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (128).to_bytes(4, "little")
    data[128:132] = b"PE\0\0"
    data[132:134] = (0x8664).to_bytes(2, "little")
    data[148:150] = (240).to_bytes(2, "little")
    data[220:222] = (2).to_bytes(2, "little")
    return data


def test_audit_packaged_helper_requires_one_root_level_x64_helper(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    helper = distribution / windows_packaging.PRODUCTION_HELPER_FILENAME
    helper.write_bytes(_minimal_x64_pe())

    assert windows_packaging.audit_packaged_helper(distribution) == helper

    nested = distribution / "_internal" / windows_packaging.PRODUCTION_HELPER_FILENAME
    nested.parent.mkdir()
    nested.write_bytes(_minimal_x64_pe())
    with pytest.raises(RuntimeError, match="exactly one root-level"):
        windows_packaging.audit_packaged_helper(distribution)


def test_audit_packaged_helper_rejects_forbidden_runtime_markers(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    helper = distribution / windows_packaging.PRODUCTION_HELPER_FILENAME
    helper.write_bytes(_minimal_x64_pe() + b"Qt6Core.dll")

    with pytest.raises(RuntimeError, match="forbidden dependency marker"):
        windows_packaging.audit_packaged_helper(distribution)


def test_audit_packaged_helper_rejects_console_subsystem(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    helper = distribution / windows_packaging.PRODUCTION_HELPER_FILENAME
    console_pe = _minimal_x64_pe()
    console_pe[220:222] = (3).to_bytes(2, "little")
    helper.write_bytes(console_pe)

    with pytest.raises(RuntimeError, match="not Windows GUI"):
        windows_packaging.audit_packaged_helper(distribution)


def test_audit_package_hygiene_rejects_native_build_outputs(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    (distribution / "_internal").mkdir(parents=True)
    (distribution / "_internal" / "debug.pdb").write_bytes(b"x")

    with pytest.raises(RuntimeError, match="forbidden files"):
        windows_packaging.audit_package_hygiene(distribution)


def test_audit_package_hygiene_rejects_native_test_and_build_tools(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "shell_helper_tests.exe").write_bytes(b"test")
    (distribution / "ninja.exe").write_bytes(b"ninja")

    with pytest.raises(RuntimeError, match="forbidden files"):
        windows_packaging.audit_package_hygiene(distribution)


def test_windows_build_script_has_staging_guard_and_native_helper_step() -> None:
    script = (_HELPER_PATH.parents[0] / "build-windows.ps1").read_text(encoding="utf-8")

    assert "OutputRoot" in script
    assert "StagingOnly" in script
    assert "officialArchiveExpectedSha256" in script
    assert "dlsite-shell-helper.exe" in script
    assert "Resolve-NativeToolchain" in script
    assert "Native CTest" in script
    assert "DLSITE_PACKAGING_OUTPUT_ROOT" in script
    assert "Compress-Archive" in script


def test_native_build_uses_static_msvc_runtime_and_production_only_package_target() -> None:
    cmake = (_HELPER_PATH.parents[1] / "native" / "shell_helper" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )

    assert "CMAKE_MSVC_RUNTIME_LIBRARY" in cmake
    assert "add_executable(dlsite-shell-helper WIN32" in cmake
    assert "src/helper_runtime.cpp" in cmake
    assert "shell_helper_tests" in cmake
    assert "shell_helper_com_probe" in cmake


def test_production_helper_uses_windows_entry_point() -> None:
    main = (_HELPER_PATH.parents[1] / "native" / "shell_helper" / "src" / "main.cpp").read_text(
        encoding="utf-8"
    )
    runtime = (
        _HELPER_PATH.parents[1] / "native" / "shell_helper" / "src" / "helper_runtime.cpp"
    ).read_text(encoding="utf-8")

    assert "wWinMain" in main
    assert "wmain" not in main
    assert "RunShellHelper" in main
    assert "CoInitializeSecurity" in runtime
