"""Native Windows offline Undo UAT. Mutates only a fresh ignored research sandbox."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from benchmark_organizer_preview import make_preview
from inspect_organizer_phase61 import NoNetworkProvider, OfflineLookup, settle
from inspect_organizer_phase63 import wait
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameOperation,
    RenameTransaction,
    TransactionStatus,
    UndoResult,
    UndoStatus,
    UndoTransactionState,
)
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.state.organizer_state import OrganizerState


class ControlledOrganizer(OrganizerService):
    fail = False

    def _preview_scan(self, scan, **kwargs):
        if self.fail:
            raise RuntimeError("Offline Phase 6.5 injected scoped refresh failure")
        return super()._preview_scan(scan, **kwargs)


def mapping_timings():
    timings = []
    for count in (1000, 5000):
        preview = make_preview(count)
        operations = tuple(RenameOperation(
            "timing", i, plan.source_path, plan.target_path,
            status=ExecutionStatus.SUCCESS, undo_status=UndoStatus.SUCCESS,
        ) for i, plan in enumerate(preview.plans) if plan.target_path is not None)
        transaction = RenameTransaction(
            "timing", preview.root_path, datetime.now(UTC), datetime.now(UTC),
            TransactionStatus.UNDONE, operations, undo_state=UndoTransactionState.SUCCESS)
        result = UndoResult(TransactionStatus.UNDONE, transaction, operations)
        renamed = replace(preview, plans=tuple(replace(
            plan, source_path=op.target_path, status=RenamePlanStatus.UNCHANGED,
        ) for plan, op in zip(preview.plans, operations, strict=True)))
        samples = []
        for _ in range(7):
            state = OrganizerState()
            state.replace_preview(renamed)
            state.completed_targets.update(state.row_keys)
            started = perf_counter()
            assert state.prepare_undo_reconciliation(result)
            samples.append((perf_counter() - started) * 1000)
            assert not state.completed_targets
            assert tuple(state.review_scope.values()) == tuple(op.source_path for op in operations)
        timings.append({"members": count, "operations": count, "samples": 7,
                        "undo_mapping_and_overlay_ms": round(median(samples), 3)})
    return timings


def main():
    output = Path.cwd() / "research" / f"phase65-native-{uuid4().hex[:8]}"
    root = output / "ABCD"
    root.mkdir(parents=True)
    paths = tuple(root / f"{letter} RJ{i:06}" for i, letter in enumerate("ABCD", 1))
    for path in paths:
        path.mkdir()
    app = QApplication([])
    assert app.platformName() == "windows"
    database = Database(output / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    service = ControlledOrganizer(OfflineLookup())
    executor, undo = RenameExecutor(journal), UndoService(journal)
    window = MainWindow(
        LookupService(NoNetworkProvider(), NamingService()), CoverService(),
        organizer_service=service, rename_executor=executor, undo_service=undo)
    window.setWindowTitle("dlsite-organizer — Phase 6.5 offline sandbox")
    page = window.organizer_page
    captures = {}
    evidence = {"platform": platform.platform(), "qt_platform": app.platformName(),
                "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", "system default"),
                "captures": captures, "human_acceptance": "PENDING"}

    def capture(name):
        settle(app)
        path = output / f"{name}.png"
        assert window.grab().save(str(path))
        captures[name] = str(path)

    def idle():
        wait(app, lambda: not page.is_busy())

    def confirm():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        dialog.button(QMessageBox.StandardButton.Yes).click()

    def execute(codes):
        page.preview_model.bulk_check("clear")
        for row, plan in enumerate(page.state.plans):
            if plan.work_code in codes:
                page.preview_model.setData(page.preview_model.index(row, 0),
                                           Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        QTimer.singleShot(30, confirm)
        page.execute_button.click()
        idle()
        result = page.state.last_execution_result
        assert result is not None and result.success_count == len(codes)
        return result

    def undo_latest():
        request = page.state.request_id
        QTimer.singleShot(30, confirm)
        started = perf_counter()
        page.undo_button.click()
        window.navigate_to(MainPage.LOOKUP)
        idle()
        window.navigate_to(MainPage.ORGANIZER)
        assert page.state.request_id == request + 1
        return round((perf_counter() - started) * 1000, 3)

    try:
        window.show()
        window.resize(1160, 800)
        settle(app)
        evidence["dpr"] = window.devicePixelRatioF()
        evidence["windows_scaling_percent"] = round(window.devicePixelRatioF() * 100)
        evidence["logical_dpi"] = window.screen().logicalDotsPerInch()
        page.set_root_path(root)
        page.scan_button.click()
        idle()
        assert tuple(page.state.review_scope.values()) == paths
        page.table.setCurrentIndex(page.preview_model.index(3, 1))
        page.remove_button.click()
        assert tuple(page.state.review_scope.values()) == paths[:3]
        execute({"RJ000001", "RJ000002"})
        assert sum(page.state.completed(i) for i in range(3)) == 2
        capture("01-before-undo-completed")
        page.set_root_path(output / "next-scan-input")
        evidence["canonical_undo_and_refresh_ms"] = undo_latest()
        assert not page.state.stale and not page.state.completed_targets
        assert tuple(p.source_path for p in page.state.plans) == paths[:3]
        assert all(p.status is RenamePlanStatus.READY for p in page.state.plans)
        assert page.root_input.text().endswith("next-scan-input")
        capture("02-after-undo-scoped-ready")
        first = execute({"RJ000001"})
        execute({"RJ000002"})
        undo_latest()
        assert page.state.completed(0) and not page.state.completed(1)
        assert tuple(page.state.review_scope.values()) == (
            first.operations[0].target_path, paths[1], paths[2])
        capture("03-unrelated-completed-survives")
        other = output / "different-root"
        other.mkdir()
        (other / "old RJ000006").mkdir()
        executor.execute(other, service.preview(other).plans, confirmed=True)
        page.refresh_mutation_state()
        preview, request, scope = page.preview, page.state.request_id, dict(page.state.review_scope)
        QTimer.singleShot(30, confirm)
        page.undo_button.click()
        idle()
        assert page.preview is preview and page.state.request_id == request and not page.state.stale
        assert page.state.review_scope == scope
        capture("04-different-root-unchanged")
        execute({"RJ000002"})
        service.fail = True
        undo_latest()
        undone = page.state.last_undo_result
        assert undone is not None and undone.status is TransactionStatus.UNDONE
        assert page.state.stale and not page.state.checks_editable
        assert not page.execute_button.isEnabled() and page.retry_refresh_button.isEnabled()
        assert page.status_banner.title_label.text() == "撤销已完成，但预览刷新失败。"
        assert page.state.completed(0) and not page.state.completed(1)
        capture("05-undo-success-refresh-failed")
        service.fail = False
        page.retry_refresh_button.click()
        idle()
        assert not page.state.stale and len(page.state.plans) == 3
        assert page.state.completed(0) and not page.state.completed(1)
        assert paths[3] not in page.state.review_scope.values()
        capture("06-retry-scoped-success")
        evidence["flows"] = dict.fromkeys((
            "successful_undo_auto_refresh", "removed_member_preservation", "completed_removal",
            "unrelated_completed_preservation", "different_root", "refresh_failure_retry",
            "hidden_page_completion", "next_scan_input_independent"), "PASS")
        evidence["mapping_timings"] = mapping_timings()
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
