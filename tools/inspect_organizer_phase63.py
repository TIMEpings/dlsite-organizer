"""Native Windows offline reconciliation UAT and bounded presentation timings.

Only disposable directories/journal under research are mutated. Real Qt workers,
confirmation, executor and journal are exercised. Captures require human acceptance.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import replace
from pathlib import Path
from statistics import median
from threading import Event
from time import monotonic, perf_counter
from uuid import uuid4

from benchmark_organizer_preview import make_preview
from inspect_organizer_phase61 import NoNetworkProvider, OfflineLookup, settle
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupFreshness, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.state.organizer_state import OrganizerState


class WarningLookup(OfflineLookup):
    def lookup(self, code):
        result = super().lookup(code)
        return replace(result, freshness=LookupFreshness.CACHE_STALE_FALLBACK) \
            if code == "RJ000003" else result


class ControlledOrganizer(OrganizerService):
    fail = False
    entered: Event | None = None
    release: Event | None = None

    def _preview(self, root_path, **kwargs):
        if self.entered is not None and self.release is not None:
            self.entered.set()
            assert self.release.wait(10)
        if self.fail:
            raise RuntimeError("Offline UAT injected refresh failure")
        return super()._preview(root_path, **kwargs)


def wait(app, predicate):
    deadline = monotonic() + 12
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Native UAT timed out"
    settle(app)


def main() -> None:
    output = Path.cwd() / "research" / f"phase63-native-{uuid4().hex[:8]}"
    root = output / "library"
    root.mkdir(parents=True)
    for i in range(1, 4):
        (root / f"old RJ{i:06}").mkdir()
    (root / "RJ000004 RJ000005").mkdir()
    app = QApplication([])
    assert app.platformName() == "windows"
    database = Database(output / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    service = ControlledOrganizer(WarningLookup())
    window = MainWindow(
        LookupService(NoNetworkProvider(), NamingService()), CoverService(),
        organizer_service=service, rename_executor=RenameExecutor(journal),
        undo_service=UndoService(journal),
    )
    page = window.organizer_page
    evidence: dict[str, object] = {
        "platform": platform.platform(), "qt": app.platformName(),
        "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", "system default"),
    }
    captures: dict[str, str] = {}
    evidence["captures"] = captures

    def capture(name):
        settle(app)
        file = output / f"{name}.png"
        assert window.grab().save(str(file))
        captures[name] = str(file)
        if page.table.isVisible():
            assert abs(sum(page.table.columnWidth(i) for i in range(5))
                       - page.table.viewport().width()) <= 2

    def idle():
        wait(app, lambda: not page.is_busy())

    def scan(path):
        page.set_root_path(path)
        page.scan_button.click()
        idle()
        assert not page.state.stale

    def check(codes):
        for row, plan in enumerate(page.state.plans):
            if plan.status is RenamePlanStatus.READY:
                page.preview_model.setData(page.preview_model.index(row, 0),
                                           Qt.CheckState.Checked if plan.work_code in codes
                                           else Qt.CheckState.Unchecked,
                                           Qt.ItemDataRole.CheckStateRole)

    def execute():
        def confirm():
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QMessageBox)
            dialog.button(QMessageBox.StandardButton.Yes).click()
        QTimer.singleShot(30, confirm)
        page.execute_button.click()

    try:
        window.show()
        window.activateWindow()
        settle(app)
        evidence["dpr"] = window.devicePixelRatioF()
        evidence["logical_dpi"] = window.screen().logicalDotsPerInch()
        assert window.devicePixelRatioF() == 1.25, "Required native 125% scaling"
        scan(root)
        assert page.root_label.width() >= page.root_label.fontMetrics().horizontalAdvance(
            page.root_label.text())
        assert page.preview_root_display.text() == str(root)
        capture("01-label-preview")
        window.resize(window.minimumWidth(), window.minimumHeight())
        settle(app)
        assert page.root_label.width() >= page.root_label.fontMetrics().horizontalAdvance(
            page.root_label.text())
        capture("02-minimum-width")
        window.resize(1160, 800)
        page.bulk_menu_button.setFocus()
        QTest.keyClick(page.bulk_menu_button, Qt.Key.Key_Return)
        wait(app, page.bulk_menu.isVisible)
        capture("03-bulk-menu")
        assert page.bulk_menu.grab().save(str(output / "bulk-menu.png"))
        captures["bulk-menu"] = str(output / "bulk-menu.png")
        QTest.keyClick(page.bulk_menu, Qt.Key.Key_Home)
        QTest.keyClick(page.bulk_menu, Qt.Key.Key_Down)
        QTest.keyClick(page.bulk_menu, Qt.Key.Key_Down)
        QTest.keyClick(page.bulk_menu, Qt.Key.Key_Return)
        assert page.state.checked_ready_count == 0
        duplicates = output / "duplicates"
        duplicates.mkdir()
        for name in ("a RJ000001", "b RJ000001", "c RJ000002"):
            (duplicates / name).mkdir()
        scan(duplicates)
        assert page.state.plans[0].status is RenamePlanStatus.CONFLICT
        page.table.setCurrentIndex(page.preview_model.index(1, 1))
        page.remove_button.click()
        assert page.state.plans[0].status is RenamePlanStatus.READY
        assert not page.state.checked(0) and page.state.checked(1)
        capture("04-remove-conflict")
        scan(root)
        assert "含警告 1" in page.summary_label.text()
        assert "不可执行 1" in page.summary_label.text()
        check({"RJ000001", "RJ000002"})
        next_input = output / "next-uncommitted-input"
        page.set_root_path(next_input)
        service.entered, service.release = Event(), Event()
        start = perf_counter()
        execute()
        wait(app, service.entered.is_set)
        assert "正在刷新预览" in page.status_banner.title_label.text()
        capture("05-refresh-in-progress")
        window.navigate_to(MainPage.LOOKUP)
        service.release.set()
        idle()
        window.navigate_to(MainPage.ORGANIZER)
        service.entered = service.release = None
        evidence["first_batch_with_controlled_capture_pause_ms"] = round(
            (perf_counter() - start) * 1000, 3)
        assert page.state.committed_preview_root == root
        assert page.root_input.text() == str(next_input)
        assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 2
        assert page.state.checked_ready_count == 0 and page.undo_button.isEnabled()
        capture("06-completed-first-batch")
        check({"RJ000003"})
        execute()
        idle()
        assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 3
        capture("07-completed-second-batch")
        scan(root)
        assert not page.state.completed_targets
        capture("08-manual-rescan-reset")
        added = root / "old RJ000006"
        added.mkdir()
        page._handle_drop_paths([added])
        idle()
        check({"RJ000006"})
        service.fail = True
        execute()
        idle()
        assert page.state.stale and page.undo_button.isEnabled()
        assert page.retry_refresh_button.isVisible() and not page.execute_button.isEnabled()
        capture("09-refresh-failed")
        service.fail = False
        page.retry_refresh_button.click()
        idle()
        assert not page.state.stale and page.state.checked_ready_count == 0
        capture("10-refresh-retry")
        outside = output / "other-root" / "old RJ000007"
        outside.mkdir(parents=True)
        page._handle_drop_paths([outside])
        assert "无法将其他根目录" in page.status_label.text()
        assert str(root) in page.status_label.text()
        capture("11-cross-root-rejected")
        evidence["flows"] = dict.fromkeys((
            "full_label", "current_preview", "bulk_menu_keyboard", "conflict_remove",
            "warning_exclusivity", "execute_auto_refresh", "completed_rows", "second_batch",
            "manual_rescan_reset", "refresh_failure_retry", "cross_root_rejection",
            "navigation_survival", "committed_root", "table_fills_without_resize",
        ), "PASS")
        timing = []
        for count in (1000, 5000):
            preview = make_preview(count)
            preview = replace(preview, plans=tuple(replace(p, status=RenamePlanStatus.UNCHANGED)
                                                  for p in preview.plans))
            state = OrganizerState()
            state.replace_preview(preview)
            state.completed_targets.update(state.row_keys[::2])
            samples: dict[str, list[float]] = {"overlay_match_ms": [], "summary_ms": []}
            page.preview_model.replace_preview(preview, reconcile=True)
            page.state.completed_targets.update(page.state.row_keys[::2])
            for _ in range(5):
                start = perf_counter()
                assert sum(state.completed(row) for row in range(count)) == count // 2
                samples["overlay_match_ms"].append((perf_counter() - start) * 1000)
                start = perf_counter()
                page._update_summary(preview)
                samples["summary_ms"].append((perf_counter() - start) * 1000)
            timing.append({"rows": count, "samples": 5,
                           **{key: round(median(values), 3) for key, values in samples.items()}})
        evidence["presentation_timings"] = timing
        # 1k real siblings, captured metadata; deletion is preview-relative only.
        removal_root = output / "remove-performance"
        removal_root.mkdir()
        for i in range(1000):
            (removal_root / f"old-{i:05}").mkdir()
        preview = make_preview(1000)
        preview = replace(preview, root_path=removal_root, plans=tuple(replace(
            p, source_path=removal_root / p.current_name, target_path=removal_root / p.proposed_name
        ) for p in preview.plans))
        first, second = preview.plans[:2]
        preview = replace(preview, plans=(
            replace(first, status=RenamePlanStatus.CONFLICT),
            replace(second, proposed_name=first.proposed_name, target_path=first.target_path,
                    status=RenamePlanStatus.CONFLICT), *preview.plans[2:],
        ))
        samples = []
        for _ in range(3):
            start = perf_counter()
            result = service.reevaluate_preview(replace(
                preview, plans=(preview.plans[0], *preview.plans[2:])))
            samples.append((perf_counter() - start) * 1000)
            assert all(p.status is RenamePlanStatus.READY for p in result.plans)
        evidence["remove_replanning_999_rows_1000_siblings_ms"] = round(median(samples), 3)
        evidence["human_acceptance"] = "PENDING"
        evidence["physical_explorer_drag"] = "NOT AVAILABLE; existing drop handler UAT used"
        file = output / "evidence.json"
        file.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(file)
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
    finally:
        if service.release is not None:
            service.release.set()
        idle()
        window.close()
        database.dispose()


if __name__ == "__main__":
    main()
