import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPEC).resolve().parents[1]
source_root = project_root / "src"
_packaging_helper_path = project_root / "packaging" / "windows_packaging.py"
_packaging_helper_spec = importlib.util.spec_from_file_location(
    "dlsite_windows_packaging", _packaging_helper_path
)
if _packaging_helper_spec is None or _packaging_helper_spec.loader is None:
    raise SystemExit(f"Packaging helper not found: {_packaging_helper_path}")
_packaging_helper = importlib.util.module_from_spec(_packaging_helper_spec)
_packaging_helper_spec.loader.exec_module(_packaging_helper)

_branding_png = project_root / "assets" / "branding" / "app_icon.png"
_branding_ico = project_root / "assets" / "branding" / "app_icon.ico"
for _branding_path in (_branding_png, _branding_ico):
    if not _branding_path.is_file():
        raise SystemExit(f"Branding asset not found: {_branding_path}")

analysis = Analysis(
    [str(source_root / "dlsite_organizer" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=collect_data_files("selectolax")
    + [
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(_branding_png), str(Path("assets") / "branding")),
    ],
    hiddenimports=collect_submodules("selectolax") + collect_submodules("sqlalchemy"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

_icu_provenance = []
for _entry in analysis.binaries:
    _destination, _source, _typecode = _entry
    if not _packaging_helper.is_icu_binary(_destination):
        continue
    _icu_provenance.append(
        {
            "destination": str(_destination),
            "source": str(_source),
            "typecode": str(_typecode),
        }
    )
    if _packaging_helper.is_foreign_provider_path(str(_source)):
        raise SystemExit(
            "Foreign ICU DLL contamination detected: "
            f"{_destination} collected from unrelated path {_source}"
        )

_packaging_helper.write_provenance_manifest(
    project_root / "build" / "dlsite-organizer-binary-provenance.json",
    _icu_provenance,
)

# PySide6 and shiboken6 ship different copies of the MSVC runtime DLLs. In a
# frozen process, loading shiboken6 first can otherwise make Qt6Core resolve
# against the wrong copy and fail with Windows error 127. Keep one consistent
# PySide6-provided copy at the onedir root, which is on the bootloader PATH.
_msvc_runtime_names = {
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_codecvt_ids.dll",
    "vccorlib140.dll",
    "vcomp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
}
_runtime_entries = {}
for _entry in analysis.binaries:
    _destination, _source, _typecode = _entry
    _name = Path(_destination).name.lower()
    if _name not in _msvc_runtime_names:
        continue
    if _name not in _runtime_entries or "PySide6" in Path(_source).parts:
        _runtime_entries[_name] = _entry
analysis.binaries = [
    _entry
    for _entry in analysis.binaries
    if Path(_entry[0]).name.lower() not in _msvc_runtime_names
]
for _entry in _runtime_entries.values():
    _name = Path(_entry[0]).name
    analysis.binaries.extend(
        [
            (_name, _entry[1], _entry[2]),
            (str(Path("shiboken6") / _name), _entry[1], _entry[2]),
        ]
    )

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="dlsite-organizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(_branding_ico),
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dlsite-organizer",
)
