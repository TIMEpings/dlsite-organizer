"""Offline native Windows Phase 6.2 captures and 5k bulk measurements.

Uses disposable ignored fixtures only. Drag evidence uses native Qt events;
physical Explorer dragging and final human acceptance remain separate UAT.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from inspect_organizer_phase61 import NoNetworkProvider, OfflineLookup, settle
from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QCursor, QDragEnterEvent, QDragLeaveEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.main_window import MainWindow


def main() -> None:
    output = Path.cwd() / "research" / f"phase62-native-{uuid4().hex[:8]}"
    root = output / "sandbox"
    root.mkdir(parents=True)
    for i in range(1, 9):
        (root / f"old RJ{i:06}").mkdir()
    app = QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("Native Windows required")
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
    evidence: dict[str, object] = {"platform": app.platformName(), "captures": []}
    captures: list[str] = []
    evidence["captures"] = captures

    def capture(name: str) -> None:
        settle(app)
        QTest.qWait(80)
        file = output / f"{name}.png"
        assert window.grab().save(str(file))
        captures.append(str(file))

    def hover(button, name: str) -> None:
        QCursor.setPos(button.mapToGlobal(button.rect().center()))
        QTest.qWait(250)
        evidence[name + "_under_mouse"] = button.underMouse()
        capture(name)

    def drag(target, name: str) -> None:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(root / "old RJ000001"))])
        enter = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(target, enter)
        assert enter.isAccepted() and page.drop_overlay.isVisible()
        capture(name)
        QCoreApplication.sendEvent(target, QDragLeaveEvent())
        assert not page.drop_overlay.isVisible()

    try:
        window.show()
        window.activateWindow()
        settle(app)
        evidence["dpr"] = window.devicePixelRatioF()
        evidence["logical_dpi"] = window.screen().logicalDotsPerInch()
        capture("01-empty")
        drag(page.empty_state, "02-empty-drag")
        hover(page.browse_button, "03-browse-hover")
        hover(page.scan_button, "04-scan-hover")
        QTest.mouseMove(page.empty_state, page.empty_state.rect().center())
        page.scan_button.setFocus()
        capture("05-scan-focus")
        page._set_loading(True)  # Safe visual projection only; no worker or mutation.
        hover(page.cancel_button, "06-cancel-hover")
        page._set_loading(False)
        preview = service.preview(root)
        statuses = (RenamePlanStatus.READY, RenamePlanStatus.READY,
                    RenamePlanStatus.CONFLICT, RenamePlanStatus.UNCHANGED,
                    RenamePlanStatus.LOOKUP_FAILED, RenamePlanStatus.INVALID_TARGET,
                    RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED)
        plans = tuple(replace(plan, status=status,
                              warnings=("离线测试：请复核缓存信息。",) if i == 1 else (),
                              error="离线测试：目标目录冲突。" if i == 2 else None)
                      for i, (plan, status) in enumerate(zip(preview.plans, statuses, strict=True)))
        page.set_preview(replace(preview, plans=plans))
        page.preview_model.setData(page.preview_model.index(1, 0), Qt.CheckState.Unchecked,
                                   Qt.ItemDataRole.CheckStateRole)
        page.table.clearSelection()
        capture("07-populated-unselected")
        page.table.setCurrentIndex(page.preview_model.index(0, 1))
        page.table.setFocus()
        capture("08-ready-selected")
        page.table.setCurrentIndex(page.preview_model.index(2, 1))
        capture("09-blocked-selected")
        page.table.setCurrentIndex(page.preview_model.index(1, 1))
        capture("10-warning-ready-selected")
        page.table.setCurrentIndex(page.preview_model.index(3, 1))
        capture("11-unchanged-selected")
        hover(page.bulk_buttons["invert"], "12-toolbar-hover")
        drag(page.table.viewport(), "13-table-drag")
        page.detail_toggle.setChecked(True)
        drag(page.detail_source_path.path_input, "14-detail-drag")
        assert not window.navigation.acceptDrops()
        evidence["sidebar_accepts_drops"] = window.navigation.acceptDrops()
        assert not window.navigation_list.viewport().acceptDrops()
        assert not window.lookup_page.acceptDrops()
        evidence["lookup_accepts_drops"] = window.lookup_page.acceptDrops()
        crop = output / "toolbar-detail.png"
        assert page.bulk_toolbar.grab().save(str(crop))
        captures.append(str(crop))
        big = tuple(replace(preview.plans[0], source_path=root / f"synthetic-{i}",
                            current_name=f"synthetic-{i}") for i in range(5000))
        page.set_preview(replace(preview, plans=big))
        page.preview_model.bulk_check("clear")
        times = {}
        for action in ("select", "invert", "clear"):
            if action == "clear":
                page.preview_model.bulk_check("select")
            start = perf_counter()
            page.preview_model.bulk_check(action)
            times[action] = round((perf_counter() - start) * 1000, 3)
        evidence["bulk_5k_ms_including_page_callbacks"] = times
        evidence["library_unchanged"] = all((root / f"old RJ{i:06}").is_dir()
                                            for i in range(1, 9))
        evidence["physical_explorer_drag"] = "NOT AVAILABLE; native Qt event UAT used"
        evidence["cancel_hover"] = "visual state projection; no live scan cancellation"
        file = output / "evidence.json"
        file.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(file)
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
    finally:
        window.close()
        database.dispose()


if __name__ == "__main__":
    main()
