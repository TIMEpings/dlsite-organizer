"""Native mixed-status History oracle using isolated, valid journal transitions."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.domain.rename_execution import RenameTransactionSummary, TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.rename_history import RenameHistoryService
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.theme import apply_light_theme


def seed_history(journal: TransactionJournal, root: Path) -> dict[str, str]:
    ids = {}
    states = ("failed", "partial", "undone", "completed", "recovery_required")
    for i, state in enumerate(states):
        when = datetime(2026, 10, 6, 1, 0, tzinfo=UTC) + timedelta(minutes=i * 5)
        pairs = tuple(
            (root / f"{state}-{n}", root / f"{state}-target-{n}")
            for n in range(2 if state == "partial" else 1)
        )
        with patch("dlsite_organizer.persistence.rename_journal.datetime") as clock:
            clock.now.return_value = when
            transaction = journal.create_transaction(root, pairs)
        tid = transaction.transaction_id
        ids[state] = tid
        for source, _ in pairs:
            source.mkdir()
        if state in {"completed", "undone"}:
            pairs[0][0].rename(pairs[0][1])
            journal.mark_operation_success(tid, 1, when)
            journal.mark_transaction_completed(tid, when)
            if state == "undone":
                journal.begin_undo(tid, when)
                pairs[0][1].rename(pairs[0][0])
                journal.mark_undo_success(tid, 1, when)
                journal.mark_transaction_undone(tid, when)
        elif state == "partial":
            pairs[0][0].rename(pairs[0][1])
            journal.mark_operation_success(tid, 1, when)
            journal.record_execution_failure(
                tid, 2, "Fixture failure", TransactionStatus.PARTIAL, when
            )
        elif state == "failed":
            journal.record_execution_failure(
                tid, 1, "Fixture failure", TransactionStatus.FAILED, when
            )
        else:
            journal.mark_recovery_required(tid, 1, "Fixture interrupted before confirmation", when)
    return ids


def main() -> int:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else ".pytest_cache/phase711_visual")
    output.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="history-phase711-"))
    database = Database(base / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = base / "library"
    root.mkdir()
    ids = seed_history(journal, root)
    before = journal.list_transactions(limit=50)
    app = QApplication([])
    apply_light_theme(app)
    window = MainWindow(
        LookupService(FakeProvider(), NamingService()),
        CoverService(),
        rename_history_service=RenameHistoryService(journal),
    )
    window.navigate_to(MainPage.HISTORY)
    page = window.rename_history_page
    screen = app.primaryScreen()
    assert screen is not None
    metrics = {
        "DPR": screen.devicePixelRatio(),
        "logical_DPI": screen.logicalDotsPerInch(),
        "scaling_percent": screen.devicePixelRatio() * 100,
        "captures": [],
    }
    samples = []
    rows = [
        RenameTransactionSummary(
            str(i),
            root,
            datetime(2026, 10, 6, tzinfo=UTC),
            TransactionStatus.COMPLETED,
            operation_count=1,
        )
        for i in range(50)
    ]
    for _ in range(12):
        page.transaction_list.clear()
        start = perf_counter()
        for row in rows:
            page._add_transaction_item(row)
        samples.append((perf_counter() - start) * 1000)
    metrics["population_50_median_ms"] = statistics.median(samples[3:])
    page.refresh_history()
    cases = iter(
        [
            ("normal_completed", 1160, 800, "completed"),
            ("normal_undone", 1160, 800, "undone"),
            ("normal_recovery", 1160, 800, "recovery_required"),
            ("wide", 1540, 900, "completed"),
            ("minimum", 960, 640, "undone"),
        ]
    )

    def next_case():
        case = next(cases, None)
        if case is None:
            assert journal.list_transactions(limit=50) == before
            (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            print(json.dumps(metrics, indent=2), flush=True)
            app.quit()
            return
        name, width, height, selected = case
        window.resize(width, height)
        page.open_transaction(ids[selected], refresh=False)
        page.transaction_list.setFocus()
        window.show()
        app.processEvents()

        def capture():
            app.processEvents()
            assert all(
                page.transaction_list.visualItemRect(page.transaction_list.item(i)).height() == 58
                for i in range(5)
            )
            assert screen.grabWindow(int(window.winId())).save(str(output / f"{name}.png"))
            metrics["captures"].append(
                {
                    "name": name,
                    "logical_size": [width, height],
                    "master_width": page.transaction_list.width(),
                    "row_height": 58,
                }
            )
            QTimer.singleShot(100, next_case)

        QTimer.singleShot(700, capture)

    QTimer.singleShot(0, next_case)
    try:
        return app.exec()
    finally:
        window.close()
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
