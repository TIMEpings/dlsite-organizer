"""Native offline Windows capture: python tools/inspect_organizer_phase61.py.

Creates a disposable library and journal under ignored .pytest_cache only. Runs
the real first/second scan worker against offline metadata, never calls Execute
or Undo, and captures native Qt MainWindows for human acceptance. PNG captures
are review evidence, not pixel tests or a substitute for human visual approval.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupResult, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.main_window import MainWindow


class OfflineLookup:
    def lookup(self, code: str) -> LookupResult:
        work = Work(workno=code, title="夜间书房・静かな時間", maker_name="测试社团")
        return LookupResult(work=work, formatted_name=f"[测试社团][{code}] {work.title}")


class NoNetworkProvider:
    def fetch_work(self, code: str) -> Work:
        raise AssertionError(f"No live provider requests permitted: {code}")


def settle(app: QApplication) -> None:
    for _ in range(6):
        app.processEvents()


def main() -> int:
    output = Path.cwd() / ".pytest_cache" / f"phase61-native-{uuid4().hex[:8]}"
    root = output / "real-library-sandbox"
    root.mkdir(parents=True)
    for i in range(1, 9):
        (root / f"old RJ{i:06}").mkdir()
    app = QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("This capture requires native Windows Qt, not offscreen rendering")
    database = Database(output / "sandbox-journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    window = MainWindow(
        LookupService(NoNetworkProvider(), NamingService()), CoverService(),
        organizer_service=OrganizerService(OfflineLookup()),
        rename_executor=RenameExecutor(journal), undo_service=UndoService(journal),
    )
    page = window.organizer_page
    evidence: dict[str, object] = {"qt_platform": app.platformName(), "captures": {}}
    captures: dict[str, object] = {}
    evidence["captures"] = captures

    def capture(name: str) -> None:
        settle(app)
        png = output / f"{name}.png"
        assert window.grab().save(str(png))
        widths = [page.table.columnWidth(i) for i in range(5)]
        assert abs(sum(widths) - page.table.viewport().width()) <= 2
        captures[name] = {
            "png": str(png), "window_size": [window.width(), window.height()],
            "viewport_size": [page.table.viewport().width(), page.table.viewport().height()],
            "column_widths": widths, "dpr": window.devicePixelRatioF(),
            "detail_expanded": page.detail_toggle.isChecked(),
        }

    def scan() -> None:
        page.scan_button.click()
        deadline = monotonic() + 10
        while page.is_busy() and monotonic() < deadline:
            QTest.qWait(10)
        assert not page.is_busy() and page.preview_model.rowCount() == 8
        settle(app)

    try:
        window.show()
        window.activateWindow()
        settle(app)
        original_size = window.size()
        assert page.table.isHidden()
        page.set_root_path(root)
        scan()
        assert window.size() == original_size
        evidence["first_scan_without_resize"] = "PASS"
        page._clear_feedback()  # Remove ephemeral scan feedback from review captures.
        capture("01-first-scan")
        first_widths = [page.table.columnWidth(i) for i in range(5)]
        scan()
        assert first_widths == [page.table.columnWidth(i) for i in range(5)]
        evidence["second_scan_consistent"] = "PASS"
        page._clear_feedback()
        page.preview_model.setData(page.preview_model.index(1, 0), Qt.CheckState.Unchecked,
                                   Qt.ItemDataRole.CheckStateRole)
        page.table.setCurrentIndex(page.preview_model.index(1, 0))
        page.table.setFocus()
        capture("02-normal-checkbox-focus")
        assert page.preview is not None
        preview = page.preview
        statuses = (
            RenamePlanStatus.READY, RenamePlanStatus.READY, RenamePlanStatus.CONFLICT,
            RenamePlanStatus.AMBIGUOUS_CODE, RenamePlanStatus.LOOKUP_FAILED,
            RenamePlanStatus.INVALID_TARGET, RenamePlanStatus.UNCHANGED,
            RenamePlanStatus.UNCHANGED,
        )
        plans = tuple(replace(
            plan, status=status, warnings=("使用旧缓存 metadata；建议复核作品信息。",)
            if i in (1, 2, 7) else (), error={
                2: "目标目录已存在，不能覆盖。", 3: "目录中存在多个作品编号。",
                4: "离线测试：查询失败。", 5: "目标名称含无效字符。",
            }.get(i),
        ) for i, (plan, status) in enumerate(zip(preview.plans, statuses, strict=True)))
        page.set_preview(replace(preview, plans=plans))
        page._clear_feedback()
        page.preview_model.setData(page.preview_model.index(1, 0), Qt.CheckState.Unchecked,
                                   Qt.ItemDataRole.CheckStateRole)
        page.table.setCurrentIndex(page.preview_model.index(2, 1))
        page.table.setFocus()
        capture("03-mixed-status-warning-detail")
        page.detail_toggle.setChecked(True)
        capture("03b-expanded-diagnostics")
        page.detail_toggle.setChecked(False)
        page.table.setCurrentIndex(page.preview_model.index(1, 0))
        page.invalidate_preview()
        page.table.setFocus()
        capture("04-disabled-check-projection")
        page.set_preview(replace(preview, plans=plans))
        page._clear_feedback()
        page.preview_model.setData(page.preview_model.index(1, 0), Qt.CheckState.Unchecked,
                                   Qt.ItemDataRole.CheckStateRole)
        window.resize(960, 680)
        page.table.setCurrentIndex(page.preview_model.index(1, 0))
        page.table.setFocus()
        capture("05-near-minimum")
        for width, height in ((1400, 900), (960, 680), (1160, 800)):
            window.resize(width, height)
            settle(app)
            assert abs(sum(page.table.columnWidth(i) for i in range(5))
                       - page.table.viewport().width()) <= 2
        window.showMaximized()
        settle(app)
        assert abs(sum(page.table.columnWidth(i) for i in range(5))
                   - page.table.viewport().width()) <= 2
        window.showNormal()
        settle(app)
        assert abs(sum(page.table.columnWidth(i) for i in range(5))
                   - page.table.viewport().width()) <= 2
        evidence["resize_maximize_restore"] = "PASS"
        evidence["screen_logical_dpi"] = window.screen().logicalDotsPerInch()
        evidence["library_unchanged"] = all((root / f"old RJ{i:06}").is_dir()
                                            for i in range(1, 9))
        evidence_path = output / "evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(evidence_path)
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
    finally:
        window.close()
        database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
