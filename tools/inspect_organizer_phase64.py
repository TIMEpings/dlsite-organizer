"""Native Windows, offline Phase 6.4 UAT; mutates only a new research sandbox."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from benchmark_organizer_preview import make_preview
from inspect_organizer_phase61 import NoNetworkProvider, OfflineLookup, settle
from inspect_organizer_phase63 import wait
from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem

from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.state.organizer_state import OrganizerState, remap_review_scope


def main() -> None:
    output = Path.cwd() / "research" / f"phase64-native-{uuid4().hex[:8]}"
    root = output / "ABCDE"
    root.mkdir(parents=True)
    paths = tuple(root / f"{letter} RJ{i:06}" for i, letter in enumerate("ABCDE", 1))
    for path in paths:
        path.mkdir()
    app = QApplication([])
    assert app.platformName() == "windows"
    database = Database(output / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    service = OrganizerService(OfflineLookup())
    window = MainWindow(
        LookupService(NoNetworkProvider(), NamingService()), CoverService(),
        organizer_service=service, rename_executor=RenameExecutor(journal),
        undo_service=UndoService(journal),
    )
    page = window.organizer_page
    captures: dict[str, str] = {}
    evidence: dict[str, object] = {
        "platform": platform.platform(), "qt_platform": app.platformName(),
        "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", "system default"),
        "captures": captures, "human_acceptance": "PENDING",
    }

    def capture(name):
        settle(app)
        path = output / f"{name}.png"
        assert window.grab().save(str(path))
        captures[name] = str(path)

    def idle():
        wait(app, lambda: not page.is_busy())

    def scan():
        page.set_root_path(root)
        page.scan_button.click()
        idle()

    def select(rows):
        page.table.clearSelection()
        selection = page.table.selectionModel()
        for row in rows:
            selection.select(page.preview_model.index(row, 1),
                             QItemSelectionModel.SelectionFlag.Select
                             | QItemSelectionModel.SelectionFlag.Rows)

    def context(row):
        point = page.table.visualRect(page.preview_model.index(row, 1)).center()
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.RightButton, pos=point)
        QApplication.sendEvent(page.table.viewport(), QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, point, page.table.viewport().mapToGlobal(point)))
        wait(app, page.preview_menu.isVisible)

    def menu_remove():
        rect = page.preview_menu.actionGeometry(page.remove_action)
        QTest.mouseClick(page.preview_menu, Qt.MouseButton.LeftButton, pos=rect.center())
        settle(app)

    try:
        window.show()
        window.activateWindow()
        window.resize(1160, 800)
        settle(app)
        evidence["dpr"] = window.devicePixelRatioF()
        evidence["native_scaling_percent"] = round(window.devicePixelRatioF() * 100)
        evidence["125_percent"] = "TESTED" if window.devicePixelRatioF() == 1.25 else "NOT TESTED"
        evidence["100_percent"] = "TESTED" if window.devicePixelRatioF() == 1.0 else "NOT TESTED"
        evidence["logical_dpi"] = window.screen().logicalDotsPerInch()
        scan()
        assert tuple(page.state.review_scope.values()) == paths
        option = QStyleOptionViewItem()
        option.initFrom(page.table)
        option.widget = page.table
        option.rect = page.table.visualRect(page.preview_model.index(0, 0))
        check_delegate = page.table.itemDelegateForColumn(0)
        indicator = check_delegate.indicator_rect(option)
        bounds = check_delegate.checkmark_points(indicator).boundingRect()
        evidence["checkbox_geometry"] = {
            "indicator_logical_size": [indicator.width(), indicator.height()],
            "checkmark_bounds_logical_size": [bounds.width(), bounds.height()],
            "stroke_logical_width": 1.7,
        }
        page.preview_model.setData(page.preview_model.index(1, 0), Qt.CheckState.Unchecked,
                                   Qt.ItemDataRole.CheckStateRole)
        page.table.setCurrentIndex(page.preview_model.index(2, 0))
        page.table.setFocus()
        capture("01-checkbox-normal-selected-focus")
        page.invalidate_preview()
        capture("02-checkbox-disabled-checked")
        scan()
        context(4)
        assert {i.row() for i in page.table.selectionModel().selectedRows()} == {4}
        capture("03-context-single")
        assert page.preview_menu.grab().save(str(output / "menu.png"))
        menu_remove()
        assert len(page.state.plans) == 4 and paths[4].is_dir()
        scan()
        select([3, 4])
        context(4)
        assert {i.row() for i in page.table.selectionModel().selectedRows()} == {3, 4}
        capture("04-context-multiple")
        menu_remove()
        assert tuple(page.state.review_scope.values()) == paths[:3]
        assert all(path.is_dir() for path in paths)
        # Canonical execute through the real confirmation, worker and journal.
        page.preview_model.bulk_check("clear")
        page.preview_model.setData(page.preview_model.index(0, 0), Qt.CheckState.Checked,
                                   Qt.ItemDataRole.CheckStateRole)

        def confirm():
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QMessageBox)
            dialog.button(QMessageBox.StandardButton.Yes).click()

        QTimer.singleShot(30, confirm)
        page.execute_button.click()
        window.navigate_to(MainPage.LOOKUP)
        idle()
        window.navigate_to(MainPage.ORGANIZER)
        result = page.state.last_execution_result
        assert result is not None and result.success_count == 1
        target = result.operations[0].target_path
        assert tuple(p.source_path for p in page.state.plans) == (target, *paths[1:3])
        assert page.state.completed(0) and page.state.checked_ready_count == 0
        capture("05-canonical-scoped-completed")
        evidence["canonical"] = {
            "initial": [p.name for p in paths],
            "after_remove": [p.name for p in paths[:3]],
            "automatic": [p.source_path.name for p in page.state.plans],
        }
        scan()
        assert len(page.state.plans) == 5 and not page.state.completed_targets
        capture("06-explicit-rescan")
        select([0, 1])
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Delete)
        assert len(page.state.plans) == 3 and len(tuple(root.iterdir())) == 5
        capture("07-delete-preview-only")
        assert not page.summary_label.toolTip()
        assert not page.preview_root_display.toolTip()
        assert page.bulk_menu_button.toolTip() and page.bulk_menu_button.accessibleName()
        for column in (2, 3, 4):
            option = QStyleOptionViewItem()
            option.initFrom(page.table)
            option.widget = page.table
            index = page.preview_model.index(0, column)
            option.rect = page.table.visualRect(index)
            delegate = page.table.itemDelegateForColumn(column)
            evidence[f"hover_column_{column}"] = delegate.tooltip_text(option, index)
        evidence["flows"] = dict.fromkeys((
            "checkbox_states_captured", "right_click_single", "right_click_multi_selection",
            "delete_highlight_only", "filesystem_not_deleted", "canonical_scope_preserved",
            "navigation", "explicit_rescan_rediscovery", "tooltip_policy",
        ), "PASS")
        timings = []
        for count in (1000, 5000):
            preview = make_preview(count)
            state = OrganizerState()
            state.replace_preview(preview)
            root_key = normalized_path_identity(preview.root_path)
            successful = {key: p.target_path
                          for key, p in zip(state.row_keys, state.plans, strict=True)
                          if p.target_path is not None}
            samples = []
            for _ in range(7):
                start = perf_counter()
                mapped = remap_review_scope(state.review_scope, preview.root_path,
                                           root_key, successful)
                assert len(mapped) == count
                samples.append((perf_counter() - start) * 1000)
            timings.append({"members": count, "samples": 7,
                            "membership_mapping_ms": round(median(samples), 3)})
        evidence["scope_timings"] = timings
        removal_root = output / "remove-performance"
        removal_root.mkdir()
        for i in range(1000):
            (removal_root / f"old-{i:05}").mkdir()
        preview = make_preview(1000)
        preview = replace(preview, root_path=removal_root, plans=tuple(replace(
            p, source_path=removal_root / p.current_name,
            target_path=removal_root / str(p.proposed_name)) for p in preview.plans))
        samples = []
        for _ in range(3):
            page.set_preview(preview)
            page.state.project_highlight([1], 1)
            start = perf_counter()
            page.remove_selected()
            samples.append((perf_counter() - start) * 1000)
            assert len(page.state.plans) == 999
        evidence["remove_reevaluate_999_rows_1000_siblings_ms"] = round(median(samples), 3)
        scan()
        capture("08-final-review")
        file = output / "evidence.json"
        file.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(file, flush=True)
        print(json.dumps(evidence, indent=2, ensure_ascii=True), flush=True)
        if "--hold" in sys.argv:
            QTimer.singleShot(300000, app.quit)
            app.exec()
    finally:
        idle()
        window.close()
        database.dispose()


if __name__ == "__main__":
    main()
