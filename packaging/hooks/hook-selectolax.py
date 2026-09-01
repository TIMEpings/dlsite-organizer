"""Collect only selectolax runtime data for the frozen application."""

from PyInstaller.utils.hooks import collect_data_files

_selectolax_data_excludes = [
    "**/*.c",
    "**/*.pxd",
    "**/*.pxi",
    "**/*.pyi",
    "**/*.pyx",
    "**/*.typed",
]

datas = collect_data_files("selectolax", excludes=_selectolax_data_excludes)
