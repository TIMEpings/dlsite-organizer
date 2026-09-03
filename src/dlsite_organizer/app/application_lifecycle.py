"""Application-level ownership, command routing, and shutdown coordination."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from dlsite_organizer.app.quick_action_controller import (
    QuickActionAdmissionStatus,
    QuickActionController,
    QuickActionRequest,
)
from dlsite_organizer.app.single_instance import (
    PROTOCOL_VERSION,
    LocalCommand,
    LocalCommandName,
    LocalReply,
    LocalReplyStatus,
)
from dlsite_organizer.services.quick_rename import QuickRenameService
from dlsite_organizer.ui.workers.quick_rename_worker import QuickRenameWorker

logger = logging.getLogger(__name__)


class _WindowLike(Protocol):
    def show(self) -> None: ...

    def hide(self) -> None: ...

    def showNormal(self) -> None: ...

    def raise_(self) -> None: ...

    def activateWindow(self) -> None: ...

    def isVisible(self) -> bool: ...

    def isMinimized(self) -> bool: ...

    def is_busy(self) -> bool: ...

    def close(self) -> bool: ...

    def set_close_handler(self, handler: Callable[[object, object], None]) -> None: ...


class _DatabaseLike(Protocol):
    def dispose(self) -> None: ...


class _CoordinatorLike(Protocol):
    def stop_server(self) -> None: ...

    def release_lock(self) -> None: ...


class QtQuickActionRunner(QObject):
    """Run one controller request through the existing Qt worker boundary."""

    finished = Signal()

    def __init__(
        self,
        service: QuickRenameService,
        request: QuickActionRequest,
        completion: Callable[[object | None, str | None], None],
        progress: Callable[[QuickActionRequest, str], None],
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._completion = completion
        self._progress = progress
        self._completion_sent = False
        self._worker_outcome_received = False
        self._worker_result: object | None = None
        self._worker_error: str | None = None
        self._started = False
        self._thread = QThread(self)
        self._thread.setObjectName(f"quick-action-{request.request_id}")
        self._worker = QuickRenameWorker(service, request.paths)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.result_ready.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        # Keep the worker outcome queued to this main-thread object before
        # asking the worker thread to stop.  This avoids a thread-finished
        # notification racing the result/failed signal during shutdown.
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)

    def start(self) -> None:
        """Start the already-composed worker thread exactly once."""
        if self._started:
            raise RuntimeError("quick action runner already started")
        self._started = True
        self._thread.start()

    @Slot(str)
    def _on_progress(self, message: str) -> None:
        if not self._completion_sent:
            self._progress(self._request, message)

    @Slot(object)
    def _on_result(self, result: object) -> None:
        self._worker_outcome_received = True
        self._worker_result = result
        self._worker_error = None

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._worker_outcome_received = True
        self._worker_result = None
        self._worker_error = message

    @Slot()
    def _on_worker_finished(self) -> None:
        self._thread.quit()

    @Slot()
    def _on_thread_finished(self) -> None:
        if not self._completion_sent:
            if self._worker_outcome_received:
                self._complete(self._worker_result, self._worker_error)
            else:
                self._complete(None, "轻量模式执行失败，详细信息已写入日志。")
        self.finished.emit()

    def _complete(self, result: object | None, error: str | None) -> None:
        if self._completion_sent:
            return
        self._completion_sent = True
        self._completion(result, error)


class QuickActionRunnerHost(QObject):
    """Keep Qt runners alive while the shared controller owns admission."""

    def __init__(self, service: QuickRenameService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._progress: Callable[[QuickActionRequest, str], None] = lambda _request, _message: None
        self._runners: dict[int, QtQuickActionRunner] = {}

    def set_progress_callback(
        self,
        callback: Callable[[QuickActionRequest, str], None],
    ) -> None:
        self._progress = callback

    def create_runner(
        self,
        request: QuickActionRequest,
        completion: Callable[[object | None, str | None], None],
    ) -> QtQuickActionRunner:
        runner = QtQuickActionRunner(
            self._service,
            request,
            completion,
            self._progress,
            parent=self,
        )
        key = id(runner)
        self._runners[key] = runner

        def cleanup() -> None:
            self._runners.pop(key, None)
            runner.deleteLater()

        runner.finished.connect(cleanup)
        return runner


class ApplicationLifecycle(QObject):
    """Coordinate the primary instance, windows, shared quick actions, and exit."""

    shutdown_completed = Signal()

    def __init__(
        self,
        application: QApplication,
        coordinator: _CoordinatorLike,
        database: _DatabaseLike,
        main_window: _WindowLike,
        lightweight_window: _WindowLike,
        quick_action_controller: QuickActionController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or application)
        self._application = application
        self._coordinator = coordinator
        self._database = database
        self._main_window = main_window
        self._lightweight_window = lightweight_window
        self.quick_action_controller = quick_action_controller
        self._current_window: _WindowLike | None = None
        self._shutdown_started = False
        self._finalized = False
        self._database_disposed = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(25)
        self._shutdown_timer.timeout.connect(self._poll_shutdown)
        self.quick_action_controller.request_finished.connect(self._quick_action_finished)
        self._main_window.set_close_handler(self.handle_window_close)
        self._lightweight_window.set_close_handler(self.handle_window_close)

    @property
    def current_window(self) -> _WindowLike | None:
        return self._current_window

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_started

    def show_full_mode(self) -> bool:
        """Switch to the existing full window without changing preferences."""
        if self._shutdown_started or self._lightweight_window.is_busy():
            return False
        self._main_window.show()
        self._lightweight_window.hide()
        self._current_window = self._main_window
        return True

    def show_lightweight_mode(self) -> bool:
        """Switch to the existing lightweight window without changing preferences."""
        if self._shutdown_started or self._main_window.is_busy():
            return False
        self._lightweight_window.show()
        self._main_window.hide()
        self._current_window = self._lightweight_window
        return True

    def show_settings(self) -> bool:
        """Return to full mode and select its existing Settings page."""
        if not self.show_full_mode():
            return False
        show_settings_page = getattr(self._main_window, "show_settings_page", None)
        if callable(show_settings_page):
            show_settings_page()
        return True

    def activate_current_window(self) -> bool:
        """Surface the actual current runtime window without changing its mode."""
        if self._shutdown_started:
            return False
        window = self._current_window or self._visible_window()
        if window is None:
            return False
        if window.isMinimized():
            window.showNormal()
        elif not window.isVisible():
            window.show()
            window.showNormal()
        else:
            window.showNormal()
        window.raise_()
        window.activateWindow()
        self._current_window = window
        return True

    def handle_command(self, command: LocalCommand) -> LocalReply:
        """Route one validated local command at the application boundary."""
        if self._shutdown_started:
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.SHUTTING_DOWN,
            )
        if command.command is LocalCommandName.ACTIVATE:
            self.activate_current_window()
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.ACCEPTED,
            )
        if command.command is LocalCommandName.QUICK_RENAME:
            try:
                admission = self.quick_action_controller.submit(command.quick_rename_paths)
            except Exception:
                logger.exception("Quick Rename IPC admission failed")
                return LocalReply(
                    PROTOCOL_VERSION,
                    command.request_id,
                    LocalReplyStatus.REJECTED,
                )
            if admission.status is QuickActionAdmissionStatus.ACCEPTED:
                self._present_quick_action()
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                _reply_status_for_admission(admission.status),
                admission.detail,
            )
        return LocalReply(
            PROTOCOL_VERSION,
            command.request_id,
            LocalReplyStatus.REJECTED,
        )

    def _present_quick_action(self) -> None:
        """Surface an admitted IPC action without changing the current mode."""
        if self._shutdown_started or self._main_window.is_busy():
            return
        if self._current_window is None:
            if not self.activate_current_window():
                self.show_lightweight_mode()
                self.activate_current_window()
            return
        self.activate_current_window()

    def handle_window_close(self, window: object, event: object) -> None:
        """Preserve existing busy warnings, then start one explicit shutdown."""
        accept = event.accept  # type: ignore[attr-defined]
        ignore = event.ignore  # type: ignore[attr-defined]
        if self._shutdown_started:
            accept()
            return
        if self._is_busy(window):
            ignore()
            widget = window if isinstance(window, QWidget) else None
            if widget is not None:
                QMessageBox.information(widget, "任务进行中", "请等待当前任务结束后再退出。")
            return
        accept()
        QTimer.singleShot(0, self.request_shutdown)

    def request_shutdown(self) -> None:
        """Begin idempotent shutdown while letting active quick work finish."""
        if self._finalized or self._shutdown_started:
            return
        self._shutdown_started = True
        self._coordinator.stop_server()
        self.quick_action_controller.begin_shutdown()
        if self._can_finalize():
            self._finalize_shutdown()
        else:
            self._shutdown_timer.start()

    def _poll_shutdown(self) -> None:
        if self._can_finalize():
            self._shutdown_timer.stop()
            self._finalize_shutdown()

    @Slot(object)
    def _quick_action_finished(self, _value: object) -> None:
        if self._shutdown_started:
            self._poll_shutdown()

    def _can_finalize(self) -> bool:
        return (
            self.quick_action_controller.active_request is None
            and not self._main_window.is_busy()
            and not self._lightweight_window.is_busy()
        )

    def _finalize_shutdown(self) -> None:
        if self._finalized or not self._can_finalize():
            return
        self._finalized = True
        self._shutdown_timer.stop()
        self._main_window.close()
        self._lightweight_window.close()
        try:
            if not self._database_disposed:
                self._database.dispose()
                self._database_disposed = True
        finally:
            self._coordinator.release_lock()
        self.shutdown_completed.emit()
        self._application.quit()

    def _visible_window(self) -> _WindowLike | None:
        if self._main_window.isVisible():
            return self._main_window
        if self._lightweight_window.isVisible():
            return self._lightweight_window
        return None

    def _is_busy(self, window: object) -> bool:
        if self.quick_action_controller.active_request is not None:
            return True
        is_busy = getattr(window, "is_busy", None)
        return bool(is_busy()) if callable(is_busy) else False


def _reply_status_for_admission(status) -> LocalReplyStatus:
    """Map application admission outcomes to the frozen IPC reply contract."""
    mapping = {
        QuickActionAdmissionStatus.ACCEPTED: LocalReplyStatus.ACCEPTED,
        QuickActionAdmissionStatus.DUPLICATE: LocalReplyStatus.DUPLICATE,
        QuickActionAdmissionStatus.QUEUE_FULL: LocalReplyStatus.QUEUE_FULL,
        QuickActionAdmissionStatus.SHUTTING_DOWN: LocalReplyStatus.SHUTTING_DOWN,
        QuickActionAdmissionStatus.REJECTED: LocalReplyStatus.REJECTED,
    }
    return mapping.get(status, LocalReplyStatus.REJECTED)
