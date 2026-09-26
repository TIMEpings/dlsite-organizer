"""Desktop application entry point."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Sequence

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from dlsite_organizer import __version__
from dlsite_organizer.app.application_lifecycle import (
    ApplicationLifecycle,
    QuickActionRunnerHost,
)
from dlsite_organizer.app.bootstrap import build_components
from dlsite_organizer.app.branding import load_application_icon
from dlsite_organizer.app.durable_handoff import DurableHandoff, publish
from dlsite_organizer.app.invocation import (
    InvocationParseError,
    LaunchMode,
    parse_invocation,
)
from dlsite_organizer.app.logging_config import configure_logging
from dlsite_organizer.app.quick_action_controller import QuickActionController
from dlsite_organizer.app.settings import (
    SettingsError,
    StartupMode,
    default_data_dir,
    load_settings,
)
from dlsite_organizer.app.single_instance import (
    PROTOCOL_VERSION,
    CoordinatorError,
    CoordinatorRole,
    InstanceCoordinator,
    LocalClientError,
    LocalCommand,
    LocalCommandClient,
    LocalCommandName,
    LocalReply,
    LocalReplyStatus,
    new_request_id,
)
from dlsite_organizer.ui.lightweight_window import LightweightWindow
from dlsite_organizer.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Qt application and return its process exit code."""
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    try:
        invocation = parse_invocation(raw_argv)
    except InvocationParseError as exc:
        application = _create_application(())
        _show_startup_error("启动参数错误", str(exc), application)
        return 2

    application = _create_application(raw_argv)
    profile_root = default_data_dir()
    legacy_command = (
        _command_for_invocation(invocation) if invocation.mode is LaunchMode.QUICK_RENAME else None
    )
    if legacy_command is not None:
        try:
            publish(profile_root, legacy_command)
        except (OSError, ValueError) as exc:
            _write_bounded_diagnostic(f"Quick Rename durable publication failed: {exc}")
            return 1
    deferred_handler = _DeferredCommandHandler()
    try:
        coordinator = InstanceCoordinator(profile_root)
        role = coordinator.start(deferred_handler, listen=False)
    except CoordinatorError as exc:
        _show_startup_error("启动失败", "无法建立应用实例：请检查数据目录权限。", application)
        _write_bounded_diagnostic(f"primary election failed: {exc}")
        return 1

    if role is CoordinatorRole.SECONDARY:
        forwarded = _run_as_secondary(
            application,
            coordinator,
            invocation,
            profile_root,
            deferred_handler,
            legacy_command,
        )
        if isinstance(forwarded, int):
            return forwarded
        coordinator = forwarded

    configure_logging(profile_root / "logs")
    logger.info(
        "Starting DLsite Organizer v%s launch_mode=%s profile=%s",
        __version__,
        invocation.mode.value,
        profile_root,
    )

    try:
        settings = load_settings()
        components = build_components(settings)
    except SettingsError as exc:
        logger.exception("Application settings are invalid")
        QMessageBox.critical(None, "配置错误", str(exc))
        coordinator.close()
        return 2
    except Exception:
        logger.exception("Application startup failed")
        QMessageBox.critical(None, "启动失败", "应用初始化失败，详细信息已写入日志。")
        coordinator.close()
        return 1

    runner_host = QuickActionRunnerHost(components.quick_rename_service, parent=application)
    quick_action_controller = QuickActionController(
        runner_host.create_runner,
        parent=application,
    )
    runner_host.set_progress_callback(quick_action_controller.report_progress)

    window = MainWindow(
        components.lookup_service,
        components.cover_service,
        update_check_service=components.update_check_service,
        organizer_service=components.organizer_service,
        rename_executor=components.rename_executor,
        undo_service=components.undo_service,
        settings_service=components.settings_service,
        quick_rename_service=components.quick_rename_service,
        explorer_integration_service=components.explorer_integration_service,
        runtime_signals=components.runtime_signals,
        rename_history_service=components.rename_history_service,
    )
    lightweight_window = LightweightWindow(
        components.quick_rename_service,
        components.undo_service,
        components.settings_service,
        runtime_signals=components.runtime_signals,
        quick_action_controller=quick_action_controller,
    )

    lifecycle = ApplicationLifecycle(
        application,
        coordinator,
        components.database,
        window,
        lightweight_window,
        quick_action_controller,
    )
    try:
        durable = DurableHandoff(
            profile_root,
            lifecycle.submit_quick_rename,
            parent=application,
            controller=quick_action_controller,
        )
    except (OSError, ValueError):
        logger.exception("Quick Rename durable handoff could not initialize")
        lifecycle.request_shutdown()
        _show_startup_error("启动失败", "Quick Rename 持久化目录不可用。", application)
        return 1
    lifecycle.durable_handoff = durable
    deferred_handler.set_handler(lifecycle.handle_command)

    window.lightweight_requested.connect(lifecycle.show_lightweight_mode)
    lightweight_window.full_mode_requested.connect(lifecycle.show_full_mode)
    lightweight_window.settings_requested.connect(lifecycle.show_settings)

    try:
        coordinator.listen()
    except CoordinatorError as exc:
        logger.exception("Primary local IPC server could not start")
        lifecycle.request_shutdown()
        _show_startup_error(
            "启动失败",
            "应用无法启动本地单实例服务；未继续运行。",
            application,
        )
        _write_bounded_diagnostic(f"local IPC server failed: {exc}")
        return 1

    if invocation.mode is LaunchMode.QUICK_RENAME:
        quick_rename_directories = invocation.quick_rename_directories
        assert quick_rename_directories
        logger.info("launch mode = quick-rename paths=%s", quick_rename_directories)
        if os.environ.get("DLSITE_ORGANIZER_QUICK_RENAME_SMOKE") == "1":
            lightweight_window.quick_action_finished.connect(
                lambda _result: QTimer.singleShot(250, lifecycle.request_shutdown)
            )
            lightweight_window.quick_action_failed.connect(
                lambda _message: QTimer.singleShot(250, lifecycle.request_shutdown)
            )
        lifecycle.show_lightweight_mode()
    elif invocation.quick_rename_host or settings.startup_mode is StartupMode.LIGHTWEIGHT:
        lifecycle.show_lightweight_mode()
    else:
        lifecycle.show_full_mode()
    QTimer.singleShot(0, durable.start)
    if invocation.mode is LaunchMode.NORMAL and not invocation.quick_rename_host:
        _schedule_startup_smoke(application, window, lightweight_window, lifecycle)
    exit_code = application.exec()
    lifecycle.request_shutdown()
    logger.info("DLsite Organizer stopped")
    return exit_code


class _DeferredCommandHandler:
    """Mutable handler installed before the primary application is composed."""

    def __init__(self) -> None:
        self._handler = None

    def set_handler(self, handler) -> None:
        self._handler = handler

    def __call__(self, command: LocalCommand) -> LocalReply:
        if self._handler is None:
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.SHUTTING_DOWN,
            )
        return self._handler(command)


def _run_as_secondary(
    application: QApplication,
    coordinator: InstanceCoordinator,
    invocation,
    profile_root,
    deferred_handler: _DeferredCommandHandler,
    legacy_command: LocalCommand | None,
) -> int | InstanceCoordinator:
    """Forward one invocation without constructing the primary runtime."""
    if invocation.handoff_id is not None:
        return _wait_for_handoff_primary(
            application, coordinator, profile_root, deferred_handler, invocation.handoff_id
        )
    command = legacy_command or _command_for_invocation(invocation)
    try:
        reply = LocalCommandClient(coordinator.identity.server_name).send(command)
    except LocalClientError as exc:
        if invocation.mode is LaunchMode.QUICK_RENAME:
            try:
                publish(profile_root, command)
            except (OSError, ValueError) as publication_error:
                coordinator.close()
                _secondary_failure(invocation, f"持久化请求失败：{publication_error}")
                return 1
            return _wait_for_handoff_primary(
                application, coordinator, profile_root, deferred_handler, command.request_id
            )
        if not exc.is_ambiguous:
            # The owner may have exited after election but before its server
            # became reachable.  Re-election is safe because no request was
            # connected or written in this classification.
            coordinator.close()
            retry_coordinator = InstanceCoordinator(profile_root)
            try:
                retry_role = retry_coordinator.start(deferred_handler, listen=False)
            except CoordinatorError:
                retry_coordinator.close()
                _secondary_failure(invocation, "无法连接到正在运行的应用。")
                return 1
            if retry_role is CoordinatorRole.PRIMARY:
                return retry_coordinator
            retry_coordinator.close()
            _secondary_failure(invocation, "无法连接到正在运行的应用。")
            return 1
        coordinator.close()
        _secondary_failure(invocation, "应用通信未完成；未执行本次 Quick Rename。")
        return 1
    finally:
        if coordinator.role is CoordinatorRole.SECONDARY:
            coordinator.close()

    if (
        reply.status in {LocalReplyStatus.SHUTTING_DOWN, LocalReplyStatus.QUEUE_FULL}
        and invocation.mode is LaunchMode.QUICK_RENAME
    ):
        try:
            publish(profile_root, command)
        except (OSError, ValueError) as publication_error:
            _secondary_failure(invocation, f"持久化请求失败：{publication_error}")
            return 1
        return _wait_for_handoff_primary(
            application, coordinator, profile_root, deferred_handler, command.request_id
        )
    if reply.status is LocalReplyStatus.ACCEPTED:
        return 0
    if reply.status is LocalReplyStatus.DUPLICATE:
        return 0
    if reply.status is LocalReplyStatus.QUEUE_FULL:
        _secondary_failure(invocation, "Quick Rename 队列已满，请稍后重试。")
    elif reply.status is LocalReplyStatus.SHUTTING_DOWN:
        _secondary_failure(invocation, "应用正在退出，未接受本次操作。")
    else:
        _secondary_failure(invocation, "应用未接受本次操作；未执行本地文件修改。")
    return 1


def _wait_for_handoff_primary(application, coordinator, profile_root, deferred_handler, handoff_id):
    """Secondary host waits outside COM until admission or lock takeover."""
    journal = profile_root / "handoff" / "v1" / "journal" / f"{handoff_id}.json"
    coordinator.close()
    while True:
        if journal.exists():
            try:
                outcome = json.loads(journal.read_text(encoding="utf-8"))
                state = outcome.get("state")
                if state == "terminal":
                    return 0 if outcome.get("status") in {"ACCEPTED", "DUPLICATE"} else 1
                if state == "indeterminate":
                    _write_bounded_diagnostic(f"Quick Rename admission indeterminate: {handoff_id}")
                    return 1
            except (OSError, ValueError):
                pass
        contender = InstanceCoordinator(profile_root)
        try:
            role = contender.start(deferred_handler, listen=False)
        except CoordinatorError:
            contender.close()
            return 1
        if role is CoordinatorRole.PRIMARY:
            return contender
        contender.close()
        application.processEvents()
        QThread.msleep(250)


def _command_for_invocation(invocation) -> LocalCommand:
    if invocation.mode is LaunchMode.NORMAL:
        return LocalCommand(
            PROTOCOL_VERSION,
            new_request_id(),
            LocalCommandName.ACTIVATE,
            {},
        )
    paths = [str(path.expanduser().absolute()) for path in invocation.quick_rename_directories]
    payload: dict[str, object] = {"path": paths[0]} if len(paths) == 1 else {"paths": paths}
    return LocalCommand(
        PROTOCOL_VERSION,
        new_request_id(),
        LocalCommandName.QUICK_RENAME,
        payload,
    )


def _secondary_failure(invocation, message: str) -> None:
    detail = (
        "无法将"
        + (" Quick Rename 请求" if invocation.mode is LaunchMode.QUICK_RENAME else "应用启动请求")
        + f"转发到活动实例：{message}"
    )
    _write_bounded_diagnostic(detail)
    if os.environ.get("DLSITE_ORGANIZER_TEST_MODE") != "1":
        QMessageBox.critical(None, "应用通信失败", detail)


def _show_startup_error(title: str, message: str, application: QApplication) -> None:
    _write_bounded_diagnostic(message)
    if os.environ.get("DLSITE_ORGANIZER_TEST_MODE") != "1":
        QMessageBox.critical(None, title, message)


def _write_bounded_diagnostic(message: str) -> None:
    sys.stderr.write(f"DLsite Organizer: {message}\n")


def _create_application(argv: Sequence[str]) -> QApplication:
    """Create Qt with the parsed application arguments and stable app identity."""
    program = sys.argv[0] if sys.argv else "dlsite-organizer"
    application = QApplication([program, *argv])
    application.setApplicationName("DLsite Organizer")
    application.setOrganizationName("dlsite-organizer")
    application.setQuitOnLastWindowClosed(False)
    application.setWindowIcon(load_application_icon())
    return application


def _schedule_startup_smoke(
    application: QApplication,
    window: MainWindow,
    lightweight_window: LightweightWindow,
    lifecycle: ApplicationLifecycle,
) -> None:
    """Run the bounded packaged-startup probe when explicitly requested.

    This hook is intentionally opt-in and has no effect during normal use.  It
    lets the release process launch the exact executable in a fresh profile,
    verify that the user-facing pages were constructed, and exit cleanly
    without requiring GUI automation or a live DLsite connection.
    """
    if os.environ.get("DLSITE_ORGANIZER_STARTUP_SMOKE") != "1":
        return

    expected_pages = ["整理", "查询", "设置", "重命名历史"]
    actual_pages = [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ]
    expected_about = f"关于 · v{__version__}"
    about_pixmap = window.about_page.branding_image.pixmap()
    lightweight_pixmap = lightweight_window.drop_zone.branding_pixmap
    icon_ready = not application.windowIcon().isNull()
    about_branding_ready = about_pixmap is not None and not about_pixmap.isNull()
    lightweight_branding_ready = (
        lightweight_pixmap is not None and not lightweight_pixmap.isNull()
    )
    if (
        actual_pages != expected_pages
        or window.about_button.text() != expected_about
        or not icon_ready
        or not about_branding_ready
        or not lightweight_branding_ready
    ):
        logger.error(
            "Startup smoke failed: navigation pages=%s about_footer=%s "
            "app_icon=%s about_branding=%s lightweight_branding=%s",
            actual_pages,
            window.about_button.text(),
            icon_ready,
            about_branding_ready,
            lightweight_branding_ready,
        )
        QTimer.singleShot(0, lambda: application.exit(3))
        return

    logger.info(
        "Startup smoke passed: MainWindow pages=%s about_footer=%s "
        "app_icon=%s about_branding=%s lightweight_branding=%s",
        actual_pages,
        window.about_button.text(),
        icon_ready,
        about_branding_ready,
        lightweight_branding_ready,
    )
    QTimer.singleShot(250, lifecycle.request_shutdown)


if __name__ == "__main__":
    raise SystemExit(main())
