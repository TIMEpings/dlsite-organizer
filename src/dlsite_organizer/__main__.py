"""Desktop application entry point."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from dlsite_organizer import __version__
from dlsite_organizer.app.bootstrap import build_components
from dlsite_organizer.app.branding import load_application_icon
from dlsite_organizer.app.invocation import (
    InvocationParseError,
    LaunchMode,
    parse_invocation,
)
from dlsite_organizer.app.logging_config import configure_logging
from dlsite_organizer.app.settings import (
    SettingsError,
    StartupMode,
    default_data_dir,
    load_settings,
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
        configure_logging(default_data_dir() / "logs")
        logger.error("Invalid application invocation: %s", exc)
        QMessageBox.critical(None, "启动参数错误", str(exc))
        return 2

    application = _create_application(raw_argv)
    configure_logging(default_data_dir() / "logs")
    logger.info(
        "Starting DLsite Organizer v%s launch_mode=%s",
        __version__,
        invocation.mode.value,
    )

    try:
        settings = load_settings()
        components = build_components(settings)
    except SettingsError as exc:
        logger.exception("Application settings are invalid")
        QMessageBox.critical(None, "配置错误", str(exc))
        return 2
    except Exception:
        logger.exception("Application startup failed")
        QMessageBox.critical(None, "启动失败", "应用初始化失败，详细信息已写入日志。")
        return 1

    window = MainWindow(
        components.lookup_service,
        components.cover_service,
        organizer_service=components.organizer_service,
        rename_executor=components.rename_executor,
        undo_service=components.undo_service,
        settings_service=components.settings_service,
        quick_rename_service=components.quick_rename_service,
        explorer_integration_service=components.explorer_integration_service,
        runtime_signals=components.runtime_signals,
    )
    lightweight_window = LightweightWindow(
        components.quick_rename_service,
        components.undo_service,
        components.settings_service,
        runtime_signals=components.runtime_signals,
    )

    def show_full_mode() -> None:
        if lightweight_window.is_busy():
            return
        window.show()
        lightweight_window.hide()

    def show_lightweight_mode() -> None:
        if window.is_busy():
            return
        lightweight_window.show()
        window.hide()

    def show_settings() -> None:
        if lightweight_window.is_busy():
            return
        window.show()
        lightweight_window.hide()
        window.show_settings_page()

    window.lightweight_requested.connect(show_lightweight_mode)
    lightweight_window.full_mode_requested.connect(show_full_mode)
    lightweight_window.settings_requested.connect(show_settings)

    if invocation.mode is LaunchMode.QUICK_RENAME:
        quick_rename_directories = invocation.quick_rename_directories
        assert quick_rename_directories
        logger.info("launch mode = quick-rename paths=%s", quick_rename_directories)
        if os.environ.get("DLSITE_ORGANIZER_QUICK_RENAME_SMOKE") == "1":
            lightweight_window.quick_action_finished.connect(
                lambda _result: QTimer.singleShot(250, application.quit)
            )
            lightweight_window.quick_action_failed.connect(
                lambda _message: QTimer.singleShot(250, application.quit)
            )
        lightweight_window.show()
        QTimer.singleShot(
            0,
            lambda: lightweight_window.start_quick_rename(quick_rename_directories),
        )
    elif settings.startup_mode is StartupMode.LIGHTWEIGHT:
        lightweight_window.show()
    else:
        window.show()
    if invocation.mode is LaunchMode.NORMAL:
        _schedule_startup_smoke(application, window, lightweight_window)
    exit_code = application.exec()
    components.database.dispose()
    logger.info("DLsite Organizer stopped")
    return exit_code


def _create_application(argv: Sequence[str]) -> QApplication:
    """Create Qt with the parsed application arguments and stable app identity."""
    program = sys.argv[0] if sys.argv else "dlsite-organizer"
    application = QApplication([program, *argv])
    application.setApplicationName("DLsite Organizer")
    application.setOrganizationName("dlsite-organizer")
    application.setWindowIcon(load_application_icon())
    return application


def _schedule_startup_smoke(
    application: QApplication,
    window: MainWindow,
    lightweight_window: LightweightWindow,
) -> None:
    """Run the bounded packaged-startup probe when explicitly requested.

    This hook is intentionally opt-in and has no effect during normal use.  It
    lets the release process launch the exact executable in a fresh profile,
    verify that the user-facing pages were constructed, and exit cleanly
    without requiring GUI automation or a live DLsite connection.
    """
    if os.environ.get("DLSITE_ORGANIZER_STARTUP_SMOKE") != "1":
        return

    expected_pages = ["整理", "查询", "设置"]
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
    QTimer.singleShot(250, application.quit)


if __name__ == "__main__":
    raise SystemExit(main())
