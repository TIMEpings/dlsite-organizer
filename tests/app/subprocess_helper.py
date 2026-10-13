"""Small isolated subprocess harness for single-instance application tests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QTimer

import dlsite_organizer.__main__ as application_main
from dlsite_organizer.persistence.database import Database


def _mark(suffix: str) -> None:
    marker = os.environ.get("DLSITE_ORGANIZER_TEST_MARKER")
    if not marker:
        return
    path = Path(marker).with_name(f"{Path(marker).name}.{suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="ascii")


def _write_marker(suffix: str, content: str) -> None:
    marker = os.environ.get("DLSITE_ORGANIZER_TEST_MARKER")
    if not marker:
        return
    path = Path(marker).with_name(f"{Path(marker).name}.{suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _event(name: str) -> None:
    marker = os.environ.get("DLSITE_ORGANIZER_TEST_MARKER")
    if marker:
        with Path(f"{marker}.events.jsonl").open("a", encoding="utf-8") as stream:
            # Python 3.12 monotonic_ns uses coarse GetTickCount64 on Windows.
            # QueryPerformanceCounter orders adjacent events across processes.
            stream.write(json.dumps({"event": name, "time_ns": time.perf_counter_ns()}) + "\n")


original_build_components = application_main.build_components


def tracked_build_components(settings):
    barrier = os.environ.get("DLSITE_ORGANIZER_TEST_BUILD_BARRIER")
    if barrier:
        _mark("startup-paused")
        while not Path(barrier).exists():
            time.sleep(0.02)
    _mark("components")
    components = original_build_components(settings)
    service = components.quick_rename_service
    original_rename = service.rename

    def tracked_rename(*args, **kwargs):
        _mark("quick")
        if args:
            _write_marker("quick-count", str(len(args[0])))
            _write_marker("quick-paths", json.dumps([str(path) for path in args[0]]))
        return original_rename(*args, **kwargs)

    service.rename = tracked_rename  # type: ignore[method-assign]
    return components


application_main.build_components = tracked_build_components  # type: ignore[assignment]

original_database_init = Database.__init__


def tracked_database_init(self, path):
    _mark("database")
    original_database_init(self, path)


Database.__init__ = tracked_database_init  # type: ignore[method-assign]

original_dispose = Database.dispose


def tracked_dispose(self):
    original_dispose(self)
    _event("database-disposed")
    _mark("database-disposed")


Database.dispose = tracked_dispose  # type: ignore[method-assign]

original_start = application_main.InstanceCoordinator.start


def tracked_start(self, *args, **kwargs):
    role = original_start(self, *args, **kwargs)
    if role is application_main.CoordinatorRole.PRIMARY:
        _event("primary-owned")
        _mark("primary-owned")
    else:
        _event("secondary-contending")
        _mark("secondary-contending")
    return role


application_main.InstanceCoordinator.start = tracked_start  # type: ignore[method-assign]

original_release_lock = application_main.InstanceCoordinator.release_lock


def tracked_release_lock(self):
    _event("lock-release")
    original_release_lock(self)
    _mark("lock-released")


application_main.InstanceCoordinator.release_lock = tracked_release_lock  # type: ignore[method-assign]

original_listen = application_main.InstanceCoordinator.listen


def tracked_listen(coordinator):
    result = original_listen(coordinator)
    if result:
        _mark("ready")
    return result


application_main.InstanceCoordinator.listen = tracked_listen  # type: ignore[method-assign]


def schedule_test_shutdown(_application, _window, _lightweight, lifecycle):
    barrier = os.environ.get("DLSITE_ORGANIZER_TEST_SHUTDOWN_GAP_BARRIER")
    if not barrier:
        return
    original_can_finalize = lifecycle._can_finalize
    deadline = time.monotonic() + 10
    gate_opened = False

    def gated_can_finalize() -> bool:
        nonlocal gate_opened
        can_finalize = original_can_finalize()
        if not Path(barrier).is_file():
            if time.monotonic() >= deadline:
                _mark("shutdown-gate-timeout")
                _application.exit(4)
            return False
        if not gate_opened:
            gate_opened = True
            _event("shutdown-gate-open")
        return can_finalize

    # Delay eligibility only. Production request_shutdown, its asynchronous
    # polling timer, and its finalizer still own all cleanup and lock release.
    lifecycle._can_finalize = gated_can_finalize
    startup_timer = QTimer(lifecycle)
    startup_timer.setInterval(25)

    def enter_gap() -> None:
        durable = lifecycle.durable_handoff
        assert durable is not None
        if not durable._timer.isActive():
            if time.monotonic() >= deadline:
                startup_timer.stop()
                _mark("shutdown-gate-timeout")
                _application.exit(4)
            return
        startup_timer.stop()
        _event("shutdown-requested")
        lifecycle.request_shutdown()
        assert lifecycle.is_shutting_down
        assert lifecycle.quick_action_controller.is_shutting_down
        assert not durable._timer.isActive()
        assert lifecycle._coordinator.server is None
        assert lifecycle._coordinator._lock.isLocked()
        _event("admission-stopped-lock-held")
        _mark("shutdown-gap")

    # Observe normal durable startup rather than assume zero-timer ordering.
    startup_timer.timeout.connect(enter_gap)
    startup_timer.start()


application_main._schedule_startup_smoke = schedule_test_shutdown

original_lifecycle_init = application_main.ApplicationLifecycle.__init__


def tracked_lifecycle_init(self, *args, **kwargs):
    original_lifecycle_init(self, *args, **kwargs)
    self.shutdown_completed.connect(lambda: _mark("shutdown-completed"))
    shutdown_file = os.environ.get("DLSITE_ORGANIZER_TEST_SHUTDOWN_FILE")
    if shutdown_file:
        timer = QTimer(self)
        timer.setInterval(25)

        def shutdown_when_signaled() -> None:
            if Path(shutdown_file).is_file():
                timer.stop()
                self.request_shutdown()

        timer.timeout.connect(shutdown_when_signaled)
        timer.start()


application_main.ApplicationLifecycle.__init__ = tracked_lifecycle_init  # type: ignore[method-assign]

original_show_lightweight_mode = application_main.ApplicationLifecycle.show_lightweight_mode


def tracked_show_lightweight_mode(self):
    _mark("lightweight")
    return original_show_lightweight_mode(self)


application_main.ApplicationLifecycle.show_lightweight_mode = tracked_show_lightweight_mode  # type: ignore[method-assign]

original_show_full_mode = application_main.ApplicationLifecycle.show_full_mode


def tracked_show_full_mode(self):
    _mark("full")
    return original_show_full_mode(self)


application_main.ApplicationLifecycle.show_full_mode = tracked_show_full_mode  # type: ignore[method-assign]

original_submit = application_main.QuickActionController.submit


def tracked_submit(self, *args, **kwargs):
    if os.environ.get("DLSITE_ORGANIZER_TEST_SHUTDOWN_FILE") and not getattr(
        self, "_test_completion_observed", False
    ):
        # DurableHandoff registered its completion slot during bootstrap. This
        # later observer runs after its journal write, so the parent need not
        # open the journal while Windows atomic replacement is still possible.
        self.request_finished.connect(lambda _outcome: _mark("controller-finished"))
        self._test_completion_observed = True
    marker = os.environ.get("DLSITE_ORGANIZER_TEST_MARKER")
    if marker:
        count_path = Path(marker).with_name(f"{Path(marker).name}.controller-submit-count")
        current = int(count_path.read_text(encoding="ascii")) if count_path.is_file() else 0
        count_path.write_text(str(current + 1), encoding="ascii")
    return original_submit(self, *args, **kwargs)


application_main.QuickActionController.submit = tracked_submit  # type: ignore[method-assign]


if __name__ == "__main__":
    raise SystemExit(application_main.main())
