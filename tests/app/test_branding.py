from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.app import branding


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def test_application_branding_assets_load_for_qt(qapp: QApplication) -> None:
    icon = branding.load_application_icon()
    pixmap = branding.load_branding_pixmap(56)

    assert not icon.isNull()
    assert pixmap is not None and not pixmap.isNull()
    assert pixmap.size().width() <= 56
    assert pixmap.size().height() <= 56


def test_missing_runtime_branding_is_safe(qapp: QApplication, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        branding,
        "application_resource_path",
        lambda _relative_path: tmp_path / "missing.png",
    )

    assert branding.load_application_icon().isNull()
    assert branding.load_branding_pixmap(56) is None
