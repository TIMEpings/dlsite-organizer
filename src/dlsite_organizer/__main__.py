"""Desktop application entry point."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

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
    logger.info("Starting DLsite Organizer v0.1.0")

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

    window = MainWindow(components.lookup_service, components.cover_service)
    window.show()
    exit_code = application.exec()
    components.database.dispose()
    logger.info("DLsite Organizer stopped")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
