"""Public About page for the desktop application."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
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
from dlsite_organizer.ui.theme import ButtonRole, set_button_role
from dlsite_organizer.ui.theme.palette import use_custom_focus_border
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS
from dlsite_organizer.ui.widgets.constrained_content import (
    ConstrainedContent,
    ContentAlignment,
)
from dlsite_organizer.ui.widgets.page_header import PageHeader
from dlsite_organizer.ui.workers.update_check_worker import (
    UpdateCheckServiceLike,
    UpdateCheckWorker,
)

logger = logging.getLogger(__name__)


class _ActionFlow(QLayout):
    """Compact action row that wraps using each control's natural size."""

    def __init__(self) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < self.count() else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), False)

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, True)

    def _arrange(self, rect: QRect, apply: bool) -> int:
        rows: list[list[tuple[QLayoutItem, QSize]]] = [[]]
        row_width = 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = self._natural_size(item)
            next_width = size.width() if not rows[-1] else row_width + self.spacing() + size.width()
            if rows[-1] and next_width > rect.width():
                rows.append([])
                row_width = 0
            if rows[-1]:
                row_width += self.spacing()
            rows[-1].append((item, size))
            row_width += size.width()

        y = rect.y()
        for row in rows:
            arranged = [(item, self._fit_width(item, size, rect.width())) for item, size in row]
            row_height = max((size.height() for _, size in arranged), default=0)
            x = rect.x()
            for item, size in arranged:
                if apply:
                    item.setGeometry(QRect(QPoint(x, y + (row_height - size.height()) // 2), size))
                x += size.width() + self.spacing()
            y += row_height + self.spacing()
        return max(0, y - rect.y() - (self.spacing() if rows else 0))

    @staticmethod
    def _natural_size(item: QLayoutItem) -> QSize:
        size = item.sizeHint().expandedTo(item.minimumSize()).boundedTo(item.maximumSize())
        widget = item.widget()
        if isinstance(widget, QLabel) and widget.wordWrap():
            margins = widget.contentsMargins()
            text_width = widget.fontMetrics().horizontalAdvance(widget.text())
            text_width += margins.left() + margins.right() + 2 * widget.margin()
            size.setWidth(max(size.width(), text_width))
            if item.hasHeightForWidth():
                size.setHeight(max(item.minimumSize().height(), item.heightForWidth(size.width())))
        return size

    @staticmethod
    def _fit_width(item: QLayoutItem, size: QSize, available_width: int) -> QSize:
        widget = item.widget()
        if (
            not isinstance(widget, QLabel)
            or not widget.wordWrap()
            or size.width() <= available_width
        ):
            return size
        width = max(1, available_width)
        height = item.heightForWidth(width) if item.hasHeightForWidth() else size.height()
        return QSize(width, max(height, item.minimumSize().height()))


class _UpdateActionButton(QPushButton):
    """Record whether an update check was activated by mouse or keyboard."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._activation_source = "other"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._activation_source = "mouse"
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # This page's action is not a dialog default button. Activate on
            # press and consume the event so Qt cannot also activate it.
            event.accept()
            if (
                not event.isAutoRepeat()
                and self.isEnabled()
                and self.isVisible()
                and self.hasFocus()
            ):
                self._activation_source = "keyboard"
                self.click()
            return
        if event.key() == Qt.Key.Key_Space:
            self._activation_source = "keyboard"
        super().keyPressEvent(event)

    def consume_activation_source(self) -> str:
        source = self._activation_source
        self._activation_source = "other"
        return source


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
        self._restore_update_button_focus = False
        self._last_update_result: UpdateCheckResult | None = None
        self._release_url: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = ConstrainedContent(
            880,
            alignment=ContentAlignment.CENTERED_INFORMATIONAL,
        )
        scroll.setAlignment(content.alignment.value | Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        content_layout = content.content_layout
        content_layout.setSpacing(24)

        header = QHBoxLayout()
        header.setSpacing(16)
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

        self.about_header = PageHeader("DLsite Organizer", f"v{__version__}")
        self.about_header.title_label.setWordWrap(True)
        version_value = self.about_header.description_label
        if version_value is None:
            raise RuntimeError("About identity header must include its version label")
        self.version_value: QLabel = version_value
        self.version_label = self.version_value
        identity_text = QVBoxLayout()
        identity_text.setSpacing(8)
        identity_text.addWidget(self.about_header)
        self.description_value = QLabel("DLsite 作品元数据查询与文件夹整理工具")
        self.description_value.setWordWrap(True)
        self.description_value.setObjectName("aboutDescriptionValue")
        identity_text.addWidget(self.description_value)
        header.addLayout(identity_text, 1)
        content_layout.addLayout(header)

        identity = QWidget()
        identity.setObjectName("aboutProductInfo")
        self.identity_box = identity
        identity_layout = QFormLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setHorizontalSpacing(24)
        identity_layout.setVerticalSpacing(8)
        identity_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.developer_value = QLabel("TIMEpings")
        self.developer_value.setObjectName("aboutDeveloperValue")
        self.developer_label = self.developer_value
        self.copyright_value = QLabel("Copyright © 2026 TIMEpings")
        self.copyright_value.setWordWrap(True)
        self.copyright_value.setObjectName("aboutCopyrightValue")
        self.copyright_label = self.copyright_value
        self.license_value = QLabel("MIT")
        self.license_value.setObjectName("aboutLicenseValue")
        identity_layout.addRow("开发与维护", self.developer_value)
        identity_layout.addRow("版权", self.copyright_value)
        identity_layout.addRow("License", self.license_value)
        content_layout.addWidget(identity)

        updates = QWidget()
        updates.setObjectName("aboutUpdateSection")
        updates.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.update_section = updates
        updates_section_layout = QVBoxLayout(updates)
        updates_section_layout.setContentsMargins(0, 0, 0, 0)
        updates_section_layout.setSpacing(8)
        updates_heading = QLabel("版本更新")
        updates_heading.setObjectName("sectionLabel")
        updates_section_layout.addWidget(updates_heading)
        updates_layout = _ActionFlow()
        updates_layout.setSpacing(12)
        updates_section_layout.addLayout(updates_layout)
        current_version_row = QHBoxLayout()
        current_version_row.addWidget(QLabel("当前版本"))
        self.update_current_version_value = QLabel(f"v{__version__}")
        self.update_current_version_value.setObjectName("aboutCurrentVersionValue")
        current_version_row.addWidget(self.update_current_version_value)
        version_row = QWidget()
        version_row.setLayout(current_version_row)
        current_version_row.setContentsMargins(0, 0, 0, 0)
        updates_layout.addWidget(version_row)

        self.release_page_button = QPushButton("查看发布页面")
        self.release_page_button.setObjectName("releasePageButton")
        self.release_page_button.setEnabled(False)
        self.release_page_button.hide()
        self.update_status_label = QLabel("尚未检查更新")
        self.update_status_label.setObjectName("statusLabel")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setMinimumHeight(32)
        updates_layout.addWidget(self.update_status_label)

        self.check_update_button = _UpdateActionButton("检查更新")
        self.check_update_button.setObjectName("checkUpdateButton")
        set_button_role(self.check_update_button, ButtonRole.SECONDARY)
        set_button_role(self.release_page_button, ButtonRole.SECONDARY)
        updates_layout.addWidget(self.release_page_button)
        updates_layout.addWidget(self.check_update_button)
        for action in (self.check_update_button, self.release_page_button):
            use_custom_focus_border(action)
            action.setStyleSheet(
                "QPushButton { min-height: 22px; padding: 4px 12px;"
                f" border: 1px solid {LIGHT_COLORS.border}; }}"
                'QPushButton[visualRole="secondary"]:hover:enabled {'
                f" background: {LIGHT_COLORS.sidebar_hover};"
                f" border: 1px solid {LIGHT_COLORS.border_strong};"
                " }"
                'QPushButton[visualRole="secondary"]:pressed:enabled {'
                f" background: {LIGHT_COLORS.selection};"
                f" border: 1px solid {LIGHT_COLORS.focus};"
                " }"
                'QPushButton[visualRole="secondary"]:disabled {'
                f" color: {LIGHT_COLORS.text_muted};"
                f" background: {LIGHT_COLORS.surface_secondary};"
                f" border: 1px solid {LIGHT_COLORS.border};"
                " }"
                f"QPushButton:focus {{ border: 1px solid {LIGHT_COLORS.focus}; }}"
            )
            # QSS dimensions describe the content box: 22 + 8 padding + 2 border.
            # Keep larger font size hints usable instead of imposing a hard cap.
            action.setMinimumHeight(32)
        content_layout.addWidget(updates)

        self.disclaimer_value = QLabel(
            "本项目为非官方工具，与 DLsite / 株式会社エイシス无官方关联。"
        )
        self.disclaimer_value.setObjectName("aboutDisclaimer")
        self.disclaimer_value.setWordWrap(True)
        self.privacy_value = QLabel(
            "应用数据保存在本机。没有遥测、分析或远程错误上报。作品查询和可选封面下载会访问 "
            "DLsite；只有用户点击“检查更新”时才会以匿名 HTTPS 访问 GitHub 公开 Releases。"
        )
        self.privacy_value.setObjectName("aboutPrivacy")
        self.privacy_value.setWordWrap(True)
        privacy_section = QWidget()
        privacy_layout = QVBoxLayout(privacy_section)
        privacy_layout.setContentsMargins(0, 0, 0, 0)
        privacy_layout.setSpacing(8)
        privacy_heading = QLabel("隐私与声明")
        privacy_heading.setObjectName("sectionLabel")
        privacy_layout.addWidget(privacy_heading)
        privacy_layout.addWidget(self.disclaimer_value)
        privacy_layout.addWidget(self.privacy_value)
        content_layout.addWidget(privacy_section)

        notices = QWidget()
        notices.setObjectName("aboutLicenseSection")
        self.notices_box = notices
        notices_layout = QVBoxLayout(notices)
        notices_layout.setContentsMargins(0, 0, 0, 0)
        notices_layout.setSpacing(8)
        notices_heading = QLabel("许可证与声明")
        notices_heading.setObjectName("sectionLabel")
        notices_layout.addWidget(notices_heading)
        license_summary = QLabel("本应用使用 MIT 许可证；第三方组件继续适用各自许可证。")
        license_summary.setWordWrap(True)
        notices_layout.addWidget(license_summary)
        buttons = _ActionFlow()
        buttons.setSpacing(4)
        self.license_button = QPushButton("LICENSE")
        self.license_button.setObjectName("licenseButton")
        set_button_role(self.license_button, ButtonRole.QUIET)
        self.third_party_notices_button = QPushButton("第三方声明")
        self.third_party_notices_button.setObjectName("thirdPartyNoticesButton")
        set_button_role(self.third_party_notices_button, ButtonRole.QUIET)
        self.third_party_licenses_button = QPushButton("第三方许可证目录")
        self.third_party_licenses_button.setObjectName("thirdPartyLicensesButton")
        set_button_role(self.third_party_licenses_button, ButtonRole.QUIET)
        buttons.addWidget(self.license_button)
        buttons.addWidget(self.third_party_notices_button)
        buttons.addWidget(self.third_party_licenses_button)
        for link in (
            self.license_button,
            self.third_party_notices_button,
            self.third_party_licenses_button,
        ):
            use_custom_focus_border(link)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setAccessibleName(link.text())
            link.setStyleSheet(
                "QPushButton { min-height: 20px; padding: 3px 5px;"
                f" color: {LIGHT_COLORS.accent}; background: transparent;"
                " border: 1px solid transparent; border-radius: 3px; }"
                "QPushButton:hover { text-decoration: underline; }"
                f"QPushButton:focus {{ border-color: {LIGHT_COLORS.focus}; }}"
                f"QPushButton:disabled {{ color: {LIGHT_COLORS.text_muted}; }}"
            )
            link.setFixedHeight(28)
        notices_layout.addLayout(buttons)
        content_layout.addWidget(notices)
        content_layout.addStretch(1)

        self.license_button.clicked.connect(lambda: self._open_resource("LICENSE", "LICENSE"))
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

        activation_source = self.check_update_button.consume_activation_source()
        self._restore_update_button_focus = (
            activation_source == "keyboard" and self.check_update_button.hasFocus()
        )
        if self.check_update_button.hasFocus():
            # Disabling a focused button makes Qt choose the next tab stop. Keep
            # focus with the update section while the action is unavailable.
            self.update_section.setFocus(Qt.FocusReason.MouseFocusReason)

        self._last_update_result = None
        self._release_url = None
        self.release_page_button.setEnabled(False)
        self.release_page_button.hide()
        set_button_role(self.check_update_button, ButtonRole.SECONDARY)
        set_button_role(self.release_page_button, ButtonRole.SECONDARY)
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
            self.check_update_button.setText("重新检查")
            set_button_role(self.check_update_button, ButtonRole.SECONDARY)
            self.check_update_button.setEnabled(True)
            self._set_update_status("检查失败，请稍后重试", "error")
            self._restore_update_focus_if_needed()

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
            set_button_role(self.check_update_button, ButtonRole.SECONDARY)
            set_button_role(self.release_page_button, ButtonRole.SECONDARY)
            self._set_update_status("检查失败，请稍后重试", "error")

        self._update_thread.quit()

    def _render_update_result(self, result: UpdateCheckResult) -> None:
        """Render only the public state of the service result."""
        self._release_url = None
        self.release_page_button.setEnabled(False)
        self.release_page_button.hide()

        if result.status is UpdateCheckStatus.UP_TO_DATE:
            set_button_role(self.check_update_button, ButtonRole.SECONDARY)
            set_button_role(self.release_page_button, ButtonRole.SECONDARY)
            self._set_update_status("已是最新版本", "success")
        elif (
            result.status is UpdateCheckStatus.UPDATE_AVAILABLE
            and result.latest_version
            and result.release_url
            and result.release_url.strip()
        ):
            self._release_url = result.release_url
            set_button_role(self.check_update_button, ButtonRole.SECONDARY)
            set_button_role(self.release_page_button, ButtonRole.PRIMARY)
            self._set_update_status(f"发现新版本 {result.latest_version}", "success")
            self.release_page_button.setEnabled(True)
            self.release_page_button.show()
        else:
            set_button_role(self.check_update_button, ButtonRole.SECONDARY)
            set_button_role(self.release_page_button, ButtonRole.SECONDARY)
            self._set_update_status("检查失败，请稍后重试", "error")

    @Slot()
    def _update_finished(self) -> None:
        """Release both owned objects only after the QThread has stopped."""
        self._update_thread = None
        self._update_worker = None
        self.check_update_button.setText("重新检查")
        self.check_update_button.setEnabled(True)
        self._restore_update_focus_if_needed()

    def _restore_update_focus_if_needed(self) -> None:
        restore_focus = self._restore_update_button_focus
        self._restore_update_button_focus = False
        if (
            restore_focus
            and self.isVisible()
            and QApplication.focusWidget() is self.update_section
            and self.check_update_button.isVisible()
            and self.check_update_button.isEnabled()
            and self.check_update_button.focusPolicy() is not Qt.FocusPolicy.NoFocus
        ):
            self.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)

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
