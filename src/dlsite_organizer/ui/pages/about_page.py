"""Public About page for the desktop application."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer import __version__
from dlsite_organizer.app.branding import load_branding_pixmap
from dlsite_organizer.app.resources import application_resource_path


class AboutPage(QWidget):
    """Show product identity, licensing, provenance, and privacy information."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(16)

        header = QHBoxLayout()
        self.branding_image = QLabel()
        self.branding_image.setObjectName("aboutBrandingImage")
        self.branding_image.setFixedSize(104, 104)
        self.branding_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.branding_image.setAccessibleName("DLsite Organizer 图标")
        pixmap = load_branding_pixmap(104)
        if pixmap is None:
            self.branding_image.hide()
        else:
            self.branding_image.setPixmap(pixmap)
        header.addWidget(self.branding_image)

        header_copy = QVBoxLayout()
        heading = QLabel("关于")
        heading.setObjectName("pageTitle")
        header_copy.addWidget(heading)
        header_copy.addWidget(QLabel("DLsite Organizer"))
        header_copy.addStretch(1)
        header.addLayout(header_copy, 1)
        layout.addLayout(header)

        identity = QGroupBox("DLsite Organizer")
        identity_layout = QFormLayout(identity)
        identity_layout.setHorizontalSpacing(20)
        self.version_value = QLabel(f"v{__version__}")
        self.version_value.setObjectName("aboutVersionValue")
        self.version_label = self.version_value
        self.description_value = QLabel("DLsite 作品元数据查询与文件夹整理工具")
        self.description_value.setWordWrap(True)
        self.description_value.setObjectName("aboutDescriptionValue")
        self.developer_value = QLabel("TIMEpings")
        self.developer_value.setObjectName("aboutDeveloperValue")
        self.developer_label = self.developer_value
        self.copyright_value = QLabel("Copyright © 2026 TIMEpings")
        self.copyright_value.setObjectName("aboutCopyrightValue")
        self.copyright_label = self.copyright_value
        self.license_value = QLabel("MIT")
        self.license_value.setObjectName("aboutLicenseValue")
        identity_layout.addRow("Version", self.version_value)
        identity_layout.addRow("简介", self.description_value)
        identity_layout.addRow("开发与维护", self.developer_value)
        identity_layout.addRow("版权", self.copyright_value)
        identity_layout.addRow("License", self.license_value)
        layout.addWidget(identity)

        self.disclaimer_value = QLabel(
            "本项目为非官方工具，与 DLsite / 株式会社エイシス无官方关联。"
        )
        self.disclaimer_value.setObjectName("aboutDisclaimer")
        self.disclaimer_value.setWordWrap(True)
        layout.addWidget(self.disclaimer_value)

        self.privacy_value = QLabel(
            "应用数据保存在本机。没有遥测或分析上报。作品查询会访问 DLsite。"
        )
        self.privacy_value.setObjectName("aboutPrivacy")
        self.privacy_value.setWordWrap(True)
        layout.addWidget(self.privacy_value)

        notices = QGroupBox("许可证与声明")
        notices_layout = QVBoxLayout(notices)
        notices_layout.addWidget(QLabel("本应用使用 MIT 许可证；第三方组件继续适用各自许可证。"))
        buttons = QHBoxLayout()
        self.license_button = QPushButton("查看 LICENSE")
        self.license_button.setObjectName("licenseButton")
        self.third_party_notices_button = QPushButton("查看 THIRD_PARTY_NOTICES")
        self.third_party_notices_button.setObjectName("thirdPartyNoticesButton")
        self.third_party_licenses_button = QPushButton("查看第三方许可证目录")
        self.third_party_licenses_button.setObjectName("thirdPartyLicensesButton")
        buttons.addWidget(self.license_button)
        buttons.addWidget(self.third_party_notices_button)
        buttons.addWidget(self.third_party_licenses_button)
        buttons.addStretch(1)
        notices_layout.addLayout(buttons)
        layout.addWidget(notices)
        layout.addStretch(1)

        self.license_button.clicked.connect(
            lambda: self._open_resource("LICENSE", "LICENSE")
        )
        self.third_party_notices_button.clicked.connect(
            lambda: self._open_resource("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES")
        )
        self.third_party_licenses_button.clicked.connect(
            lambda: self._open_resource("licenses", "第三方许可证目录")
        )

    @Slot()
    def _open_resource(self, relative_path: str, display_name: str) -> None:
        """Open a packaged/source resource through Qt's safe URL boundary."""
        try:
            path = application_resource_path(relative_path)
        except ValueError:
            self._show_resource_error(display_name)
            return
        if not path.exists():
            self._show_resource_error(display_name)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._show_resource_error(display_name)

    def _show_resource_error(self, display_name: str) -> None:
        QMessageBox.warning(self, "无法打开资源", f"未找到或无法打开 {display_name}。")
