"""Read-only, selectable path display with an optional explicit copy action."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QWidget

from dlsite_organizer.ui.theme.stylesheet import set_button_role
from dlsite_organizer.ui.theme.tokens import ButtonRole


class PathDisplay(QWidget):
    """Show a full path in a single-line field that supports selection and copy."""

    def __init__(
        self,
        *,
        copy_button: bool = True,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pathDisplay")
        self.setProperty("compact", compact)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.path_input = QLineEdit(self)
        self.path_input.setReadOnly(True)
        self.path_input.setClearButtonEnabled(False)
        self.path_input.setObjectName("pathDisplayInput")
        self.path_input.setAccessibleName("完整路径")
        self.path_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.path_input, 1)

        self.copy_button: QPushButton | None = None
        if copy_button:
            self.copy_button = QPushButton("复制", self)
            self.copy_button.setObjectName("copyPathButton")
            self.copy_button.setAccessibleName("复制完整路径")
            self.copy_button.setToolTip("复制完整路径")
            self.copy_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            set_button_role(self.copy_button, ButtonRole.QUIET)
            self.copy_button.clicked.connect(self.copy_to_clipboard)
            layout.addWidget(self.copy_button)
        if compact:
            self.setStyleSheet(
                """
                QWidget#pathDisplay[compact="true"] QLineEdit#pathDisplayInput {
                  min-height: 24px;
                  padding: 2px 6px;
                }
                QWidget#pathDisplay[compact="true"] QPushButton#copyPathButton {
                  min-height: 24px;
                  padding: 2px 7px;
                }
                """
            )

    def set_path(self, value: str) -> None:
        """Set the complete path shown by the control."""
        self.path_input.setText(value)
        self.path_input.setToolTip(value)
        self.path_input.setCursorPosition(0)
        self.setAccessibleDescription(value)
        if self.copy_button is not None:
            self.copy_button.setEnabled(bool(value))

    def text(self) -> str:
        """Return the complete path, independent of the visible line width."""
        return self.path_input.text()

    def copy_to_clipboard(self) -> None:
        """Copy the complete, unelided path."""
        if self.path_input.text():
            QGuiApplication.clipboard().setText(self.path_input.text())
