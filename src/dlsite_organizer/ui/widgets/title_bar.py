"""Compact full-mode identity and caption controls; no lifecycle ownership."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QToolButton, QWidget

from dlsite_organizer.app.branding import load_application_icon
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS, Geometry, Spacing


class CaptionButton(QAbstractButton):
    """DPI-independent line glyphs with client and native hover presentation."""

    def __init__(self, action: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.action = action
        self.restored = True
        self.native_hover = False
        self.setFixedSize(Geometry.CAPTION_WIDTH, Geometry.TITLE_BAR_HEIGHT)
        # Native captions are outside the page Tab sequence. Click focus still
        # permits Space activation and retains a visible focus indicator.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self.update_accessibility()

    def update_accessibility(self) -> None:
        label = {"minimize": "最小化", "maximize": "最大化", "close": "关闭"}[self.action]
        if self.action == "maximize" and not self.restored:
            label = "还原"
        self.setAccessibleName(label)
        self.setToolTip(label)

    def set_native_hover(self, hovered: bool) -> None:
        if self.native_hover != hovered:
            self.native_hover = hovered
            self.update()

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self.update()
        return super().event(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        colors = LIGHT_COLORS
        painter = QPainter(self)
        hovered = self.native_hover or self.underMouse()
        background = colors.surface
        foreground = colors.text_primary
        if self.isDown():
            background = colors.border if self.action != "close" else colors.blocked
        elif hovered:
            background = colors.surface_secondary if self.action != "close" else colors.error
        if self.action == "close" and (hovered or self.isDown()):
            foreground = colors.surface
        painter.fillRect(self.rect(), QColor(background))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(foreground), 1.0))
        center = QRectF(self.rect()).center()
        x, y = center.x() - 5, center.y() - 5
        if self.action == "minimize":
            painter.drawLine(QPoint(round(x), round(y + 5)), QPoint(round(x + 10), round(y + 5)))
        elif self.action == "close":
            painter.drawLine(QPoint(round(x), round(y)), QPoint(round(x + 10), round(y + 10)))
            painter.drawLine(QPoint(round(x + 10), round(y)), QPoint(round(x), round(y + 10)))
        elif self.restored:
            painter.drawRect(QRectF(x, y, 10, 10))
        else:
            painter.drawRect(QRectF(x + 2, y, 8, 8))
            painter.fillRect(QRectF(x, y + 2, 8, 8), QColor(background))
            painter.drawRect(QRectF(x, y + 2, 8, 8))
        if self.hasFocus():
            painter.setPen(QPen(QColor(colors.focus), 1.0))
            painter.drawRect(QRectF(self.rect()).adjusted(3, 3, -3, -3))


class TitleBar(QWidget):
    """Emit window requests and present state supplied by the owning MainWindow."""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(Geometry.TITLE_BAR_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.SM, 0, 0, 1)
        layout.setSpacing(0)
        self.icon_button = QToolButton(self)
        self.icon_button.setObjectName("systemMenuIcon")
        self.icon_button.setFixedSize(28, 32)
        self.icon_button.setIcon(load_application_icon())
        self.icon_button.setIconSize(QSize(20, 20))
        self.icon_button.setAccessibleName("窗口系统菜单")
        self.icon_button.setToolTip("窗口系统菜单 (Alt+Space)")
        self.icon_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.icon_button)
        self.title_label = QLabel("DLsite Organizer", self)
        self.title_label.setObjectName("windowTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addSpacing(Spacing.SM)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self.minimize_button = CaptionButton("minimize", self)
        self.maximize_button = CaptionButton("maximize", self)
        self.close_button = CaptionButton("close", self)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.minimize_button.clicked.connect(self.minimize_requested)
        self.maximize_button.clicked.connect(self.maximize_requested)
        self.close_button.clicked.connect(self.close_requested)

    def set_maximized(self, maximized: bool) -> None:
        self.maximize_button.restored = not maximized
        self.maximize_button.update_accessibility()
        self.maximize_button.update()

    def set_product_icon(self, icon: QIcon) -> None:
        self.icon_button.setIcon(icon)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(LIGHT_COLORS.surface))
        painter.setPen(QColor(LIGHT_COLORS.border))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
