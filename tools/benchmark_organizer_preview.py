"""Offline Phase 5 Qt diagnostic: python tools/benchmark_organizer_preview.py.

Run with --interactive 1000 (or 5000) for a real Windows MainWindow inspection.
No provider, filesystem mutation, user library, settings persistence or timing
assertions are involved. Output reports median milliseconds across five samples.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableView, QWidget

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus, ScanResult
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import OrganizerState
from dlsite_organizer.ui.theme import apply_light_theme


def make_preview(count: int) -> OrganizerPreview:
    root = Path.cwd() / ".pytest_cache" / "phase5-synthetic"
    work = Work(workno="RJ000001", title="离线中文作品・日本語 title", maker_name="社团")
    plans = tuple(RenamePlan(
        source_path=root / f"old-{i:05}", current_name=f"old-{i:05}",
        work_code=work.workno, work=work, proposed_name=f"目标名称-{i:05}",
        target_path=root / f"目标名称-{i:05}", status=RenamePlanStatus.READY,
    ) for i in range(count))
    return OrganizerPreview(root, ScanResult(root, (), ()), (), plans)


class OfflineProvider:
    def fetch_work(self, workno: str) -> Work:
        raise AssertionError(f"Synthetic inspection must not query a provider: {workno}")


def benchmark(app: QApplication, count: int) -> dict[str, object]:
    preview = make_preview(count)  # Already-produced service plans: excluded from timers.
    samples: dict[str, list[float]] = {
        "state_build_ms": [], "model_reset_including_state_ms": [],
        "first_presentation_ms": [], "single_check_ms": [],
    }
    for _ in range(5):
        state = OrganizerState()
        started = perf_counter()
        state.replace_preview(preview)
        samples["state_build_ms"].append((perf_counter() - started) * 1000)
        model = OrganizerPreviewModel(state)
        view = QTableView()
        view.resize(960, 420)
        view.setModel(model)
        view.verticalHeader().setDefaultSectionSize(40)
        baseline_widgets = len(view.findChildren(QWidget))
        started = perf_counter()
        model.replace_preview(preview)
        samples["model_reset_including_state_ms"].append((perf_counter() - started) * 1000)
        started = perf_counter()
        view.show()
        app.processEvents()
        view.viewport().grab()  # Force visible viewport painting; no formal frame-rate claim.
        samples["first_presentation_ms"].append((perf_counter() - started) * 1000)
        assert len(view.findChildren(QWidget)) == baseline_widgets
        index = model.index(count // 2, 0)
        started = perf_counter()
        model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        samples["single_check_ms"].append((perf_counter() - started) * 1000)
        view.close()
        view.deleteLater()
        app.processEvents()
    return {"rows": count, **{name: round(median(values), 3) for name, values in samples.items()},
            "cell_widgets": 0, "samples": 5, "qt_platform": app.platformName()}


def benchmark_page(app: QApplication, count: int) -> dict[str, object]:
    """Measure the actual Organizer delegates/layout/detail, not just a bare table."""
    apply_light_theme(app)
    preview = make_preview(count)
    samples: dict[str, list[float]] = {
        "accepted_preview_ms": [], "first_presentation_ms": [],
        "single_check_ms": [], "detail_update_ms": [],
    }
    for _ in range(5):
        page = OrganizerPage(OrganizerService(LookupService(OfflineProvider(), NamingService())))
        page.resize(1160, 800)
        page.show()
        for _ in range(6):
            app.processEvents()
        baseline_widgets = len(page.table.findChildren(QWidget))
        started = perf_counter()
        page.set_preview(preview)
        samples["accepted_preview_ms"].append((perf_counter() - started) * 1000)
        started = perf_counter()
        for _ in range(6):
            app.processEvents()
        page.table.viewport().grab()
        samples["first_presentation_ms"].append((perf_counter() - started) * 1000)
        assert len(page.table.findChildren(QWidget)) == baseline_widgets
        assert abs(sum(page.table.columnWidth(i) for i in range(5))
                   - page.table.viewport().width()) <= 2
        index = page.preview_model.index(count // 2, 0)
        started = perf_counter()
        page.preview_model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        samples["single_check_ms"].append((perf_counter() - started) * 1000)
        # Current-row projection/diagnostics are independent of total plan count.
        page.state.project_highlight((), count // 2)
        started = perf_counter()
        page._refresh_detail(count // 2)
        samples["detail_update_ms"].append((perf_counter() - started) * 1000)
        page.close()
        page.deleteLater()
        app.processEvents()
    return {"rows": count, **{name: round(median(values), 3) for name, values in samples.items()},
            "cell_widgets": 0, "samples": 5, "qt_platform": app.platformName()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive", type=int, choices=(1000, 5000))
    args = parser.parse_args()
    app = QApplication([])
    if args.interactive:
        window = MainWindow(LookupService(OfflineProvider(), NamingService()), CoverService())
        # MainWindow applies the initial runtime settings before constructing pages.
        window.organizer_page.set_preview(replace(
            make_preview(args.interactive),
            settings_generation=window.organizer_page._organizer_service.settings_generation,
        ))
        window.show()
        return app.exec()
    print(json.dumps({
        "bare_table": [benchmark(app, count) for count in (1000, 5000)],
        "organizer_page": [benchmark_page(app, count) for count in (1000, 5000)],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
