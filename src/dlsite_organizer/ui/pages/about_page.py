"""Public About page for the desktop application."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThread, QUrl, Slot
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
from dlsite_organizer.services.update_checker import (
    UpdateCheckResult,
    UpdateCheckService,
    UpdateCheckStatus,
)
from dlsite_organizer.ui.workers.update_check_worker import (
    UpdateCheckServiceLike,
    UpdateCheckWorker,
)

logger = logging.getLogger(__name__)


class AboutPage(QWidget):
    """Show product identity, licensing, provenance, and privacy information."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        update_check_service: UpdateCheckServiceLike | None = None,
    ) -> None:
        super().__init__(parent)
        self._update_check_service = (
            update_check_service if update_check_service is not None else UpdateCheckService()
        )
        self._update_thread: QThread | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._last_update_result: UpdateCheckResult | None = None
        self._release_url: str | None = None
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

        updates = QGroupBox("版本更新")
        updates_layout = QVBoxLayout(updates)
        current_version_row = QHBoxLayout()
        current_version_row.addWidget(QLabel("当前版本"))
        self.update_current_version_value = QLabel(f"v{__version__}")
        self.update_current_version_value.setObjectName("aboutCurrentVersionValue")
        current_version_row.addWidget(self.update_current_version_value)
        current_version_row.addStretch(1)
        updates_layout.addLayout(current_version_row)

        update_actions = QHBoxLayout()
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.setObjectName("checkUpdateButton")
        self.release_page_button = QPushButton("查看发布页")
        self.release_page_button.setObjectName("releasePageButton")
        self.release_page_button.setEnabled(False)
        self.release_page_button.hide()
        update_actions.addWidget(self.check_update_button)
        update_actions.addWidget(self.release_page_button)
        update_actions.addStretch(1)
        updates_layout.addLayout(update_actions)

        self.update_status_label = QLabel("尚未检查更新")
        self.update_status_label.setObjectName("statusLabel")
        self.update_status_label.setWordWrap(True)
        updates_layout.addWidget(self.update_status_label)
        layout.addWidget(updates)

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
        self.check_update_button.clicked.connect(self.start_update_check)
        self.release_page_button.clicked.connect(self._open_release_page)

    @Slot()
    def start_update_check(self) -> None:
        """Start one manual release check unless another is still active."""
        if self._update_thread is not None or self._update_worker is not None:
            return

        self._last_update_result = None
        self._release_url = None
        self.release_page_button.setEnabled(False)
        self.release_page_button.hide()
        self.check_update_button.setEnabled(False)
        self._set_update_status("正在检查…", "loading")

        thread: QThread | None = None
        worker: UpdateCheckWorker | None = None
        try:
            thread = QThread()
            thread.setObjectName("about-update-check")
            worker = UpdateCheckWorker(self._update_check_service)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(self._handle_update_result)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._update_finished)
            self._update_thread = thread
            self._update_worker = worker
            thread.start()
        except Exception:
            logger.exception("Failed to start update check")
            if thread is not None:
                thread.quit()
                if thread.isRunning():
                    thread.wait()
                thread.deleteLater()
            self._update_thread = None
            self._update_worker = None
            self.check_update_button.setEnabled(True)
            self._set_update_status("检查失败，请稍后重试", "error")

    @Slot(object)
    def _handle_update_result(self, value: object) -> None:
        """Render one worker result, then request the owned thread to stop."""
        if self._update_thread is None:
            return

        if isinstance(value, UpdateCheckResult):
            self._last_update_result = value
            self._render_update_result(value)
        else:
            self._last_update_result = None
            self._release_url = None
            self.release_page_button.setEnabled(False)
            self.release_page_button.hide()
            self._set_update_status("检查失败，请稍后重试", "error")

        self._update_thread.quit()

    def _render_update_result(self, result: UpdateCheckResult) -> None:
        """Render only the public state of the service result."""
        self._release_url = None
        self.release_page_button.setEnabled(False)
        self.release_page_button.hide()

        if result.status is UpdateCheckStatus.UP_TO_DATE:
            self._set_update_status("已是最新版本", "success")
        elif (
            result.status is UpdateCheckStatus.UPDATE_AVAILABLE
            and result.latest_version
            and result.release_url
            and result.release_url.strip()
        ):
            self._release_url = result.release_url
            self._set_update_status(f"发现新版本 {result.latest_version}", "success")
            self.release_page_button.setEnabled(True)
            self.release_page_button.show()
        else:
            self._set_update_status("检查失败，请稍后重试", "error")

    @Slot()
    def _update_finished(self) -> None:
        """Release both owned objects only after the QThread has stopped."""
        self._update_thread = None
        self._update_worker = None
        self.check_update_button.setEnabled(True)

    @Slot()
    def _open_release_page(self) -> None:
        """Open only the validated release URL after an explicit user click."""
        if not self._release_url or self.release_page_button.isHidden():
            return
        url = QUrl(self._release_url)
        if not url.isValid() or not url.toString():
            return
        QDesktopServices.openUrl(url)

    def is_busy(self) -> bool:
        """Return whether the manual update check still owns live Qt objects."""
        return self._update_thread is not None or self._update_worker is not None

    def _set_update_status(self, text: str, state: str) -> None:
        self.update_status_label.setProperty("state", state)
        self.update_status_label.setText(text)
        self.update_status_label.style().unpolish(self.update_status_label)
        self.update_status_label.style().polish(self.update_status_label)

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
