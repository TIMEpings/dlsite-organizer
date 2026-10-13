from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.app.application_lifecycle import ApplicationLifecycle
from dlsite_organizer.app.durable_handoff import DurableHandoff, publish
from dlsite_organizer.app.quick_action_controller import (
    QuickActionController,
    QuickActionRequest,
)
from dlsite_organizer.app.single_instance import (
    CoordinatorRole,
    InstanceCoordinator,
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


def test_admitted_ipc_quick_action_preserves_existing_full_surface(
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
    assert full.visible
    assert not lightweight.visible
    assert lifecycle.current_window is full


def test_oversized_ipc_quick_action_is_rejected_with_batch_limit_feedback(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    controller = QuickActionController(lambda _request, _completion: FakeRunner(None, None))  # type: ignore[arg-type]
    lifecycle, _full, _lightweight, _events = _lifecycle(qapp, controller)
    paths = [str((tmp_path / f"RJ{index:08d} old").absolute()) for index in range(33)]

    reply = lifecycle.handle_command(
        LocalCommand(1, "oversized", LocalCommandName.QUICK_RENAME, {"paths": paths})
    )

    assert reply.status is LocalReplyStatus.REJECTED
    assert reply.detail == "一次最多处理 32 个文件夹。"
    assert controller.active_request is None


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


@pytest.mark.parametrize("busy_window", ["full", "lightweight"])
def test_durable_shutdown_finishes_admitted_work_before_disposal_and_ownership_release(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    busy_window: str,
) -> None:
    events: list[str] = []
    runners: list[FakeRunner] = []

    def factory(request: QuickActionRequest, completion) -> FakeRunner:
        runner = FakeRunner(request, completion)
        runners.append(runner)
        return runner

    controller = QuickActionController(factory)
    owner = InstanceCoordinator(tmp_path)
    full, lightweight = FakeWindow("full"), FakeWindow("lightweight")
    window = full if busy_window == "full" else lightweight
    window.busy = True
    lifecycle = ApplicationLifecycle(
        qapp, owner, FakeDatabase(events), full, lightweight, controller
    )
    durable = DurableHandoff(tmp_path, lifecycle.submit_quick_rename, controller=controller)
    lifecycle.durable_handoff = durable
    assert owner.start(lifecycle.handle_command) is CoordinatorRole.PRIMARY
    durable.start()
    original_stop, original_server_stop = durable.stop, owner.stop_server
    original_release = owner.release_lock

    def stop_admission() -> None:
        assert lifecycle.is_shutting_down
        original_stop()
        assert not durable._timer.isActive()
        events.append("durable.stop")

    def stop_server() -> None:
        assert events == ["durable.stop"]
        original_server_stop()
        assert owner.server is None
        events.append("server.stop")

    def release_lock() -> None:
        assert events[-1] == "database.dispose"
        assert controller.active_request is None
        assert not controller.pending_requests
        assert not window.busy
        assert full.closed and lightweight.closed
        original_release()
        events.append("lock.release")

    monkeypatch.setattr(durable, "stop", stop_admission)
    monkeypatch.setattr(owner, "stop_server", stop_server)
    monkeypatch.setattr(owner, "release_lock", release_lock)

    def command(request_id: str) -> LocalCommand:
        return LocalCommand(
            1, request_id, LocalCommandName.QUICK_RENAME,
            {"paths": [str(tmp_path / request_id)]},
        )

    contender = InstanceCoordinator(tmp_path)
    successor = InstanceCoordinator(tmp_path)
    try:
        for request_id in ("active", "queued"):
            assert lifecycle.handle_command(command(request_id)).status is LocalReplyStatus.ACCEPTED
        assert len(runners) == 1
        assert controller.queue_depth == 1

        lifecycle.request_shutdown()

        assert events == ["durable.stop", "server.stop"]
        assert controller.is_shutting_down
        assert controller.queue_depth == 1
        pending_request = command("successor-only")
        pending = publish(tmp_path, pending_request)
        assert lifecycle.handle_command(pending_request).status is LocalReplyStatus.SHUTTING_DOWN
        assert (
            lifecycle.submit_quick_rename(pending_request).status is LocalReplyStatus.SHUTTING_DOWN
        )
        assert contender.start(listen=False) is CoordinatorRole.SECONDARY

        runners[0].finish("first done")
        assert len(runners) == 2
        assert controller.active_request == runners[1].request
        assert events == ["durable.stop", "server.stop"]
        runners[1].finish("second done")
        qapp.processEvents()
        assert events == ["durable.stop", "server.stop"]
        assert pending.is_file()
        assert not (durable.journal / "successor-only.json").exists()
        for request_id in ("active", "queued"):
            record = json.loads((durable.journal / f"{request_id}.json").read_text())
            assert record["state"] == "terminal"
            assert record["execution"] == "completed"

        window.busy = False
        lifecycle._poll_shutdown()
        assert events == ["durable.stop", "server.stop", "database.dispose", "lock.release"]
        assert successor.start(listen=False) is CoordinatorRole.PRIMARY
        successor_controller = QuickActionController(factory)
        successor_lifecycle = ApplicationLifecycle(
            qapp, successor, FakeDatabase([]), FakeWindow("new-full"),
            FakeWindow("new-lightweight"), successor_controller,
        )
        successor_durable = DurableHandoff(
            tmp_path, successor_lifecycle.submit_quick_rename, controller=successor_controller
        )
        successor_lifecycle.durable_handoff = successor_durable
        successor_durable.drain()
        assert len(runners) == 3
        assert runners[2].request.paths == (tmp_path / "successor-only",)
        runners[2].finish("successor done")
        record = json.loads((durable.journal / "successor-only.json").read_text())
        assert record["state"] == "terminal"
        assert record["execution"] == "completed"
        assert not pending.exists()
        successor_lifecycle.request_shutdown()
    finally:
        original_stop()
        lifecycle._shutdown_timer.stop()
        contender.close()
        successor.close()
        owner.close()


def test_server_stop_and_manual_unlock_leave_old_durable_admission_active(
    qapp: QApplication, tmp_path: Path,
) -> None:
    controller = QuickActionController(lambda request, done: FakeRunner(request, done))
    owner = InstanceCoordinator(tmp_path)
    lifecycle = ApplicationLifecycle(
        qapp, owner, FakeDatabase([]), FakeWindow("full"), FakeWindow("lightweight"), controller
    )
    durable = DurableHandoff(tmp_path, lifecycle.submit_quick_rename, controller=controller)
    lifecycle.durable_handoff = durable
    successor = InstanceCoordinator(tmp_path)
    assert owner.start(lifecycle.handle_command) is CoordinatorRole.PRIMARY
    durable.start()
    try:
        # Reproduce the former helper's invalid transition as a negative control.
        owner.stop_server()
        owner.release_lock()
        assert successor.start(listen=False) is CoordinatorRole.PRIMARY
        assert not lifecycle.is_shutting_down
        assert not controller.is_shutting_down
        assert durable._timer.isActive()
        request = LocalCommand(
            1, "old-fixture-overlap", LocalCommandName.QUICK_RENAME,
            {"paths": [str(tmp_path / "unexpected-old-work")]},
        )
        publish(tmp_path, request)
        durable.drain()
        assert controller.active_request is not None
        assert controller.active_request.paths == tuple(map(Path, request.quick_rename_paths))
        controller.complete(controller.active_request)
    finally:
        if controller.active_request is not None:
            controller.complete(controller.active_request)
        lifecycle.request_shutdown()
        successor.close()
        owner.close()
