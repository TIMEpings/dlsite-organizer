"""Shared application branding resource helpers."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap

from dlsite_organizer.app.resources import application_resource_path

logger = logging.getLogger(__name__)

APP_ICON_PNG = Path("assets") / "branding" / "app_icon.png"


def load_application_icon() -> QIcon:
    """Load the shared application icon, returning a safe null icon on failure."""
    path = application_resource_path(APP_ICON_PNG)
    if not path.is_file():
        logger.warning("Application branding PNG is missing: %s", path)
        return QIcon()

    icon = QIcon(str(path))
    if icon.isNull():
        logger.warning("Application branding PNG could not be loaded: %s", path)
        return QIcon()
    return icon


def load_branding_pixmap(size: int) -> QPixmap | None:
    """Load and smoothly scale the optional branding image for a UI widget."""
    if size <= 0:
        raise ValueError("Branding image size must be positive")

    path = application_resource_path(APP_ICON_PNG)
    if not path.is_file():
        logger.warning("Optional branding image is missing: %s", path)
        return None

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        logger.warning("Optional branding image could not be loaded: %s", path)
        return None
    return pixmap.scaled(
        QSize(size, size),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
