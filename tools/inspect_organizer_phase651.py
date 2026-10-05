"""Interactive native Undo UAT with real runtime signals; isolated offline sandbox.

Run this tool, click Undo and confirm, then wait for feedback to clear. Create the
printed sandbox's prepare-success file to remove the controlled preflight conflict;
click Undo again to verify automatic scoped reconciliation. Evidence is saved there.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from time import monotonic
from uuid import uuid4

from inspect_organizer_phase61 import NoNetworkProvider, OfflineLookup
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.domain.rename_execution import TransactionStatus, UndoStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.main_window import MainWindow


def main():
    output = Path.cwd() / "research" / f"phase651-native-{uuid4().hex[:8]}"
    root = output / "library"
    root.mkdir(parents=True)
    originals = tuple(root / f"old RJ{i:06}" for i in range(1, 5))
    for path in originals:
        path.mkdir()
    app = QApplication([])
    assert app.platformName() == "windows"
    database = Database(output / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    signals = RuntimeSignals()
    executor = RenameExecutor(
        journal, mutation_history_changed=signals.notify_mutation_history_changed)
    undo = UndoService(journal, mutation_history_changed=signals.notify_mutation_history_changed)
    service = OrganizerService(OfflineLookup())
    window = MainWindow(
        LookupService(NoNetworkProvider(), NamingService()), CoverService(),
        organizer_service=service, rename_executor=executor, undo_service=undo,
        runtime_signals=signals)
    window.setWindowTitle("DLsite Organizer — Phase 6.5.1 offline sandbox")
    page = window.organizer_page
    execution = executor.execute(root, service.preview(root).plans[:1], confirmed=True)
    assert execution.status is TransactionStatus.COMPLETED
    app.processEvents()
    operation = execution.operations[0]
    operation.source_path.mkdir()  # Historical source occupancy: real zero-mutation preflight.
    scope = (operation.target_path, *originals[1:3])  # Fourth member intentionally excluded.
    page.set_preview(service.preview_paths(root, scope))
    preview, plans = page.preview, page.state.plans
    keys, request = page.state.row_keys, page.state.request_id
    checks = tuple(page.state.checked(i) for i in range(len(plans)))
    eligible = page.execute_button.isEnabled()
    assert eligible
    refreshes, notifications, invalidations = [], [], []
    refresh = service.refresh_preview_scope
    invalidate = page._invalidate_preview

    def scoped(*args, **kwargs):
        refreshes.append(str(args[0]))
        return refresh(*args, **kwargs)

    def invalidated(reason):
        invalidations.append(str(reason))
        invalidate(reason)

    service.refresh_preview_scope = scoped
    page._invalidate_preview = invalidated
    signals.mutation_history_changed.connect(lambda: notifications.append(True))
    evidence = {"platform": platform.platform(), "qt_platform": app.platformName(),
                "sandbox": str(output), "captures": {}, "phase": "ready-preflight"}
    stage, failed_at = "preflight", None

    def save():
        (output / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    def capture(name):
        path = output / f"{name}.png"
        assert window.grab().save(str(path))
        evidence["captures"][name] = str(path)

    def unchanged():
        assert page.state.preview_current and page.state.stale_reason is None
        assert page.preview is preview and page.state.plans == plans and page.state.row_keys == keys
        assert tuple(page.state.checked(i) for i in range(len(plans))) == checks
        assert tuple(page.state.review_scope.values()) == scope
        assert page.state.request_id == request and not refreshes and not invalidations
        assert page.status_banner.isHidden() and page.execute_button.isEnabled() == eligible
        assert page.state.checks_editable

    def poll():
        nonlocal stage, failed_at
        try:
            result = page.state.last_undo_result
            if stage == "preflight" and result is not None and not page.is_busy():
                assert result.status is TransactionStatus.FAILED and result.success_count == 0
                assert all(op.undo_status is UndoStatus.CONFLICT for op in result.operations)
                assert "整批未执行" in page.status_label.text() and notifications
                unchanged()
                capture("02-preflight-failure-current")
                failed_at = monotonic()
                stage = "waiting-clear"
                evidence["phase"] = "failure-visible"
                save()
            elif stage == "waiting-clear" and not page._feedback_timer.isActive():
                unchanged()
                assert not page.status_label.text() and failed_at is not None
                capture("03-feedback-cleared-current")
                evidence.update(preflight="PASS", timer_wait_seconds=monotonic() - failed_at,
                                zero_mutation_refreshes=len(refreshes),
                                zero_mutation_invalidations=len(invalidations),
                                phase="failure-cleared")
                save()
                stage = "prepare-success"
            elif stage == "prepare-success" and (output / "prepare-success").exists():
                operation.source_path.rmdir()  # Remove only our empty sandbox conflict.
                page.state.last_undo_result = None
                stage = "success"
                evidence["phase"] = "ready-success"
                save()
            elif stage == "success" and result is not None and not page.is_busy():
                assert result.status is TransactionStatus.UNDONE and result.success_count == 1
                assert len(refreshes) == 1 and page.state.request_id == request + 1
                assert page.state.preview_current and tuple(page.state.review_scope.values()) == (
                    operation.source_path, *scope[1:])
                assert tuple(p.source_path for p in page.state.plans) == tuple(
                    page.state.review_scope.values())
                assert originals[3] not in page.state.review_scope.values()
                assert operation.source_path.is_dir() and not operation.target_path.exists()
                capture("04-success-scoped-reconciliation")
                evidence.update(success="PASS", successful_refreshes=len(refreshes),
                                phase="complete")
                save()
                stage = "complete"
        except Exception as exc:
            evidence.update(phase="FAILED", error=repr(exc))
            save()
            timer.stop()
            raise

    window.show()
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    QTimer.singleShot(500, lambda: capture("01-current-before-undo"))
    save()
    print(output, flush=True)
    try:
        app.exec()
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
