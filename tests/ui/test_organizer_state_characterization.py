"""Behavior contract first run against the legacy table, then the model/view page."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem
from tests.services.test_lookup import FakeProvider
from tests.ui.test_navigation_characterization import _select_navigation_label
from tests.ui.test_organizer_page import (
    BlockingLookupService,
    FakeLookupService,
    _wait_for_scan_idle,
)

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage, _ScanMode
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorkerResult


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


def check(page: OrganizerPage, row: int, checked: bool) -> None:
    item = cast(QTableWidgetItem, page.table.item(row, 0))
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


def checked(page: OrganizerPage, row: int) -> bool:
    return cast(QTableWidgetItem, page.table.item(row, 0)).checkState() == Qt.CheckState.Checked


@pytest.fixture
def sources(tmp_path: Path) -> list[Path]:
    paths = [tmp_path / f"old RJ{index:06}" for index in range(1, 5)]
    for path in paths:
        path.mkdir()
    return paths


def test_replace_order_defaults_and_input_root_is_not_committed_root(
    qapp: QApplication, tmp_path: Path, sources: list[Path]
) -> None:
    service = OrganizerService(FakeLookupService())
    preview = service.preview_paths(tmp_path, (sources[2], sources[0], sources[1]))
    preview = replace(preview, plans=(preview.plans[2], replace(
        preview.plans[0], status=RenamePlanStatus.UNCHANGED
    ), preview.plans[1]))
    page = OrganizerPage(service)
    page.set_root_path(tmp_path)
    page.set_preview(preview)
    page.set_root_path(tmp_path / "other")
    assert page.preview is preview
    assert page.preview.root_path == tmp_path
    assert [plan.source_path for plan in page.preview.plans] == [sources[2], sources[0], sources[1]]
    assert [checked(page, row) for row in range(3)] == [True, False, True]
    page.close()


def test_append_upserts_in_place_and_preserves_checks_with_new_rows_unchecked(
    qapp: QApplication, tmp_path: Path, sources: list[Path]
) -> None:
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    initial = service.preview_paths(tmp_path, (sources[1], sources[0]))
    page.set_preview(replace(initial, plans=initial.plans[::-1]))
    check(page, 0, False)
    incoming = service.preview_paths(tmp_path, (sources[0], sources[1], sources[2]))
    incoming = replace(incoming, plans=(replace(incoming.plans[0], proposed_name="updated"),
        incoming.plans[1], incoming.plans[2]))
    page._scan_mode = _ScanMode.APPEND
    page._show_preview(OrganizerWorkerResult(0, page._preview_revision, incoming))
    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == [*sources[1::-1], sources[2]]
    assert page.preview.plans[1].proposed_name == "updated"
    assert [checked(page, row) for row in range(3)] == [False, True, False]
    blocked = replace(incoming, plans=(replace(incoming.plans[0],
        status=RenamePlanStatus.CONFLICT),))
    page._show_preview(OrganizerWorkerResult(0, page._preview_revision, blocked))
    assert not checked(page, 1)
    assert not page.selected_ready_plans()
    page.close()


def test_highlight_is_independent_and_multi_removal_preserves_remaining_checks(
    qapp: QApplication, tmp_path: Path, sources: list[Path]
) -> None:
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview(tmp_path))
    check(page, 0, False)
    selection = page.table.selectionModel()
    for row in (0, 2):
        selection.select(page.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert not checked(page, 0) and checked(page, 1)
    page.remove_selected()
    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == [sources[1], sources[3]]
    assert [checked(page, row) for row in range(2)] == [True, True]
    assert not selection.selectedRows()
    assert all(source.exists() for source in sources)
    page.close()


def test_stale_retains_rows_checks_and_selection_and_disables_execution(
    qapp: QApplication, tmp_path: Path, sources: list[Path]
) -> None:
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview(tmp_path))
    check(page, 1, False)
    page.table.selectRow(1)
    old = page.preview
    page.invalidate_preview()
    assert page.preview is old
    assert [checked(page, row) for row in range(4)] == [True, False, True, True]
    assert not page.table.isEnabled()
    assert page.table.selectionModel().selectedRows()[0].row() == 1
    assert not page.selected_ready_plans() and not page.execute_button.isEnabled()
    page.close()


def test_cancelled_replace_with_previous_preview_preserves_root_and_checks(
    qapp: QApplication, tmp_path: Path, sources: list[Path]
) -> None:
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview_paths(tmp_path, (sources[0], sources[1])))
    check(page, 1, False)
    old = page.preview
    incoming = replace(service.preview(tmp_path), cancelled=True)
    page._show_preview(OrganizerWorkerResult(0, page._preview_revision, incoming))
    assert page.preview is old and not page.preview_stale
    assert [checked(page, row) for row in range(2)] == [True, False]
    page.close()


def test_navigation_retains_check_highlight_current_and_hidden_worker_commits_once(
    qapp: QApplication, tmp_path: Path, sources: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = BlockingLookupService()
    service = OrganizerService(lookup)
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService(),
        organizer_service=service)
    page = window.organizer_page
    page.set_root_path(tmp_path)
    page.set_preview(OrganizerService(FakeLookupService()).preview(tmp_path))
    check(page, 1, False)
    page.table.selectRow(1)
    revision = page._preview_revision
    current = page.table.currentIndex()
    _select_navigation_label(window, "查询")
    _select_navigation_label(window, "整理")
    assert page._preview_revision == revision
    assert page.table.currentIndex() == current
    assert not checked(page, 1)
    assert page.table.selectionModel().selectedRows()[0].row() == 1
    delivered = []
    original = page._show_preview
    monkeypatch.setattr(
        page, "_show_preview", lambda value: (delivered.append(value), original(value))
    )
    try:
        page.start_scan()
        assert lookup.entered.wait(2)
        thread = page._thread
        _select_navigation_label(window, "查询")
        assert page._thread is thread and page._preview_revision == revision
        lookup.release.set()
        _wait_for_scan_idle(qapp, page)
        assert len(delivered) == 1 and page._preview_revision == revision + 1
        _select_navigation_label(window, "整理")
        assert window.organizer_page is page
        assert [checked(page, row) for row in range(4)] == [True] * 4
    finally:
        lookup.release.set()
        _wait_for_scan_idle(qapp, page)
        window.close()
