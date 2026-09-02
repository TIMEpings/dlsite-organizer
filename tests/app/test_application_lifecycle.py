from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.app.application_lifecycle import ApplicationLifecycle
from dlsite_organizer.app.quick_action_controller import (
    QuickActionController,
    QuickActionRequest,
)
from dlsite_organizer.app.single_instance import (
    LocalCommand,
    LocalCommandName,
    LocalReplyStatus,
)


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


class FakeWindow:
    def __init__(self, name: str) -> None:
        self.name = name
        self.visible = False
        self.minimized = False
        self.busy = False
        self.closed = False
        self.close_handler = None
        self.show_normal_calls = 0
        self.raise_calls = 0
        self.activate_calls = 0
        self.show_calls = 0
        self.hide_calls = 0

    def show(self) -> None:
        self.show_calls += 1
        self.visible = True
        self.closed = False

    def hide(self) -> None:
        self.hide_calls += 1
        self.visible = False

    def showNormal(self) -> None:
        self.show_normal_calls += 1
        self.minimized = False
        self.visible = True

    def raise_(self) -> None:
        self.raise_calls += 1

    def activateWindow(self) -> None:
        self.activate_calls += 1

    def isVisible(self) -> bool:
        return self.visible

    def isMinimized(self) -> bool:
        return self.minimized

    def is_busy(self) -> bool:
        return self.busy

    def set_close_handler(self, handler) -> None:
        self.close_handler = handler

    def close(self) -> bool:
        self.closed = True
        self.visible = False
        return True


class FakeDatabase:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def dispose(self) -> None:
        self.events.append("database.dispose")


class FakeCoordinator:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def stop_server(self) -> None:
        self.events.append("server.stop")

    def release_lock(self) -> None:
        self.events.append("lock.release")


@dataclass
class FakeRunner:
    request: QuickActionRequest
    completion: object

    def start(self) -> None:
        return

    def finish(self, result: object | None = None, error: object | None = None) -> None:
        self.completion(result, error)  # type: ignore[operator]


def _lifecycle(qapp: QApplication, controller: QuickActionController):
    events: list[str] = []
    full = FakeWindow("full")
    lightweight = FakeWindow("lightweight")
    lifecycle = ApplicationLifecycle(
        qapp,
        FakeCoordinator(events),
        FakeDatabase(events),
        full,
        lightweight,
        controller,
    )
    return lifecycle, full, lightweight, events


def test_activate_targets_current_runtime_window_without_switching_mode(
    qapp: QApplication,
) -> None:
    controller = QuickActionController(lambda _request, _completion: FakeRunner(None, None))  # type: ignore[arg-type]
    lifecycle, full, lightweight, _events = _lifecycle(qapp, controller)

    assert lifecycle.show_full_mode()
    full.minimized = True
    reply = lifecycle.handle_command(
        LocalCommand(1, "activate1", LocalCommandName.ACTIVATE, {})
    )

    assert reply.status is LocalReplyStatus.ACCEPTED
    assert full.visible
    assert not lightweight.visible
    assert full.show_normal_calls == 1
    assert full.raise_calls == 1
    assert full.activate_calls == 1


def test_activate_targets_lightweight_runtime_window_without_switching_mode(
    qapp: QApplication,
) -> None:
    controller = QuickActionController(lambda _request, _completion: FakeRunner(None, None))  # type: ignore[arg-type]
    lifecycle, full, lightweight, _events = _lifecycle(qapp, controller)

    assert lifecycle.show_lightweight_mode()
    lightweight.minimized = True
    reply = lifecycle.handle_command(
        LocalCommand(1, "activate2", LocalCommandName.ACTIVATE, {})
    )

    assert reply.status is LocalReplyStatus.ACCEPTED
    assert lightweight.visible
    assert not full.visible
    assert lightweight.show_normal_calls == 1
    assert lightweight.raise_calls == 1
    assert lightweight.activate_calls == 1


def test_ipc_status_mapping_keeps_business_outcomes_distinct(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    controller = QuickActionController(lambda _request, _completion: FakeRunner(None, None))  # type: ignore[arg-type]
    lifecycle, _full, _lightweight, _events = _lifecycle(qapp, controller)
    first = str((tmp_path / "X").absolute())

    accepted = lifecycle.handle_command(
        LocalCommand(1, "accepted", LocalCommandName.QUICK_RENAME, {"path": first})
    )
    duplicate = lifecycle.handle_command(
        LocalCommand(1, "duplicate", LocalCommandName.QUICK_RENAME, {"path": first})
    )
    controller.begin_shutdown()
    shutting_down = lifecycle.handle_command(
        LocalCommand(1, "shutdown", LocalCommandName.QUICK_RENAME, {"path": first})
    )

    assert accepted.status is LocalReplyStatus.ACCEPTED
    assert duplicate.status is LocalReplyStatus.DUPLICATE
    assert shutting_down.status is LocalReplyStatus.SHUTTING_DOWN


def test_admitted_ipc_quick_action_uses_existing_lightweight_surface(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    controller = QuickActionController(lambda _request, _completion: FakeRunner(None, None))  # type: ignore[arg-type]
    lifecycle, full, lightweight, _events = _lifecycle(qapp, controller)
    lifecycle.show_full_mode()

    reply = lifecycle.handle_command(
        LocalCommand(
            1,
            "quick-surface",
            LocalCommandName.QUICK_RENAME,
            {"path": str((tmp_path / "X").absolute())},
        )
    )

    assert reply.status is LocalReplyStatus.ACCEPTED
    assert lightweight.visible
    assert not full.visible
    assert lifecycle.current_window is lightweight


def test_local_and_ipc_quick_actions_share_one_controller_and_one_batch_queue(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    runners: list[FakeRunner] = []

    def factory(request: QuickActionRequest, completion) -> FakeRunner:
        runner = FakeRunner(request, completion)
        runners.append(runner)
        return runner

    controller = QuickActionController(factory)
    lifecycle, _full, _lightweight, _events = _lifecycle(qapp, controller)
    first = tmp_path / "X"
    second = tmp_path / "Y"

    assert controller.submit(first).accepted
    reply = lifecycle.handle_command(
        LocalCommand(
            1,
            "quick1",
            LocalCommandName.QUICK_RENAME,
            {"path": str(second.absolute())},
        )
    )

    assert reply.status is LocalReplyStatus.ACCEPTED
    assert len(runners) == 1
    assert controller.queue_depth == 1
    assert controller.pending_requests[0].path == second.absolute()


def test_shutdown_stops_admission_discards_pending_then_releases_database_before_lock(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    runners: list[FakeRunner] = []

    def factory(request: QuickActionRequest, completion) -> FakeRunner:
        runner = FakeRunner(request, completion)
        runners.append(runner)
        return runner

    controller = QuickActionController(factory)
    lifecycle, full, lightweight, events = _lifecycle(qapp, controller)
    controller.submit(tmp_path / "X")
    controller.submit(tmp_path / "Y")

    lifecycle.request_shutdown()

    assert events == ["server.stop"]
    assert controller.queue_depth == 0
    assert controller.submit(tmp_path / "Z").status.value == "shutting_down"
    assert len(runners) == 1

    runners[0].finish("done")
    qapp.processEvents()

    assert full.closed
    assert lightweight.closed
    assert events == ["server.stop", "database.dispose", "lock.release"]
    lifecycle.request_shutdown()
    assert events == ["server.stop", "database.dispose", "lock.release"]
