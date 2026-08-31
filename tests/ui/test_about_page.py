from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from dlsite_organizer import __version__
from dlsite_organizer.ui.pages.about_page import AboutPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def test_about_page_exposes_public_identity_and_resource_actions(qapp: QApplication) -> None:
    page = AboutPage()

    assert page.branding_image.objectName() == "aboutBrandingImage"
    pixmap = page.branding_image.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    assert page.version_value.text() == f"v{__version__}"
    assert page.description_value.text() == "DLsite 作品元数据查询与文件夹整理工具"
    assert page.developer_value.text() == "dlsite-organizer contributors"
    assert page.copyright_value.text() == "Copyright © 2026 dlsite-organizer contributors"
    assert page.license_value.text() == "MIT"
    assert "非官方工具" in page.disclaimer_value.text()
    assert {
        "查看 LICENSE",
        "查看 THIRD_PARTY_NOTICES",
        "查看第三方许可证目录",
    } <= {button.text() for button in page.findChildren(QPushButton)}
    page.close()
