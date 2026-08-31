"""Minimal PySide6 frozen-import reproducer used by the release gate."""

from PySide6 import QtCore

print(f"QtCore import: PASS (Qt {QtCore.qVersion()})")
