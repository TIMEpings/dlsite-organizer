"""Desktop application entry point."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from dlsite_organizer import __version__
from dlsite_organizer.app.bootstrap import build_components
from dlsite_organizer.app.logging_config import configure_logging
from dlsite_organizer.app.settings import SettingsError, default_data_dir, load_settings
from dlsite_organizer.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def main() -> int:
    """Start the Qt application and return its process exit code."""
    application = QApplication(sys.argv)
    application.setApplicationName("DLsite Organizer")
    application.setOrganizationName("dlsite-organizer")
    configure_logging(default_data_dir() / "logs")
    logger.info("Starting DLsite Organizer v%s", __version__)

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
        components.organizer_service,
        components.rename_executor,
        components.undo_service,
        components.candidate_review_queue_service,
        components.settings_service,
    )
    window.show()
    _schedule_startup_smoke(application, window)
    exit_code = application.exec()
    components.database.dispose()
    logger.info("DLsite Organizer stopped")
    return exit_code


def _schedule_startup_smoke(application: QApplication, window: MainWindow) -> None:
    """Run the bounded packaged-startup probe when explicitly requested.

    This hook is intentionally opt-in and has no effect during normal use.  It
    lets the release process launch the exact executable in a fresh profile,
    verify that the user-facing pages were constructed, and exit cleanly
    without requiring GUI automation or a live DLsite connection.
    """
    if os.environ.get("DLSITE_ORGANIZER_STARTUP_SMOKE") != "1":
        return

    expected_pages = ["整理", "查询", "候选审阅", "关系", "设置"]
    actual_pages = [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ]
    if actual_pages != expected_pages:
        logger.error("Startup smoke failed: unexpected navigation pages: %s", actual_pages)
        QTimer.singleShot(0, lambda: application.exit(3))
        return

    logger.info("Startup smoke passed: MainWindow pages=%s", actual_pages)
    QTimer.singleShot(250, application.quit)


if __name__ == "__main__":
    raise SystemExit(main())
