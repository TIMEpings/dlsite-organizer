"""Runtime signals shared by the application's windows."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class RuntimeSignals(QObject):
    """Small notification seam for state shared by long-lived UI windows."""

    mutation_history_changed = Signal()

    @Slot()
    def notify_mutation_history_changed(self) -> None:
        """Notify subscribers that journal-backed mutation state may have changed."""
        self.mutation_history_changed.emit()
