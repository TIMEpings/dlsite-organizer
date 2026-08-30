from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPEC).resolve().parents[1]
source_root = project_root / "src"

analysis = Analysis(
    [str(source_root / "dlsite_organizer" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=collect_data_files("selectolax"),
    hiddenimports=collect_submodules("selectolax") + collect_submodules("sqlalchemy"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="dlsite-organizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
