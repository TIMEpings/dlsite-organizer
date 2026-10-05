"""Offline regression for the complete preview truth after individual drops."""

from dataclasses import replace
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from tests.ui.test_organizer_page import FakeLookupService, _wait_for, _wait_for_scan_idle
from tests.ui.test_organizer_reconciliation import ControlledOrganizer

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.lookup import LookupFreshness
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_planner import BATCH_CONFLICT_WARNING
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import ScanMode
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorkerResult


@pytest.fixture
def qapp():
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


def test_individual_drop_detects_duplicate_before_execute(qapp, tmp_path):
    paths = tuple(tmp_path / f"{letter} RJ000001" for letter in "AB")
    for path in paths:
        path.mkdir()
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    try:
        page.drop_zone.paths_dropped.emit([paths[0]])
        _wait_for_scan_idle(qapp, page)
        assert page.state.plans[0].status is RenamePlanStatus.READY
        assert page.state.checked(0)
        page.drop_zone.paths_dropped.emit([paths[1]])
        _wait_for_scan_idle(qapp, page)
        assert page.state.plans[0].target_path == page.state.plans[1].target_path
        assert [p.status for p in page.state.plans] == [RenamePlanStatus.CONFLICT] * 2
        assert not any(page.state.checked(i) for i in range(2))
    finally:
        page.close()


def test_manual_scan_then_append_conflict(qapp, tmp_path):
    a, b = (tmp_path / f"{letter} RJ000001" for letter in "AB")
    a.mkdir()
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_root_path(tmp_path)
    try:
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)
        assert page.state.plans[0].status is RenamePlanStatus.READY
        b.mkdir()
        drop(qapp, page, b)
        assert [p.status for p in page.state.plans] == [RenamePlanStatus.CONFLICT] * 2
    finally:
        page.close()


def test_failed_append_reconciliation_keeps_original_atomic(qapp, tmp_path, monkeypatch):
    a, b = (tmp_path / f"{letter} RJ000001" for letter in "AB")
    a.mkdir()
    b.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview_paths(tmp_path, [a]))
    page.table.setCurrentIndex(page.preview_model.index(0, 1))
    previous = (page.preview, dict(page.state.review_scope), page.state.current_key,
                page.state.preview_revision)

    def fail(_preview, **_kwargs):
        raise OSError("controlled reconciliation failure")

    monkeypatch.setattr(service, "reevaluate_preview", fail)
    try:
        drop(qapp, page, b)
        assert (page.preview, page.state.review_scope, page.state.current_key,
                page.state.preview_revision) == previous
        assert page.state.checked(0) and page.state.preview_current
        assert "当前预览保持不变" in page.status_label.text()
    finally:
        page.close()


def test_execution_preflight_still_rejects_external_occupancy(qapp, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    a, b, valid = (root / name for name in ("A RJ000001", "B RJ000001", "V RJ000002"))
    for path in (a, b, valid):
        path.mkdir()
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    service = OrganizerService(FakeLookupService())
    executor = RenameExecutor(TransactionJournal(database))
    page = OrganizerPage(service, execution_service=executor)
    try:
        independent = [service.preview_paths(root, [path]).plans[0] for path in (a, b)]
        # Characterize the old internal inconsistency: preflight rejects it without mutation.
        old_failure = executor.execute(root, independent, confirmed=True)
        assert old_failure.status is TransactionStatus.FAILED and old_failure.success_count == 0
        assert a.is_dir() and b.is_dir()
        for path in (a, b, valid):
            drop(qapp, page, path)
        assert [p.status for p in page.state.plans] == [
            RenamePlanStatus.CONFLICT, RenamePlanStatus.CONFLICT, RenamePlanStatus.READY]
        page.preview_model.bulk_check("select")
        selected = page.selected_ready_plans()
        assert [p.source_path for p in selected] == [valid]
        target = selected[0].target_path
        assert target is not None
        target.mkdir()  # Real external race after the current preview was accepted.
        failed = executor.execute(root, selected, confirmed=True)
        assert failed.status is TransactionStatus.FAILED and failed.success_count == 0
        assert valid.is_dir()
        page._show_execution_result(failed)
        assert page.state.stale  # Execute failure invalidation was deliberately preserved.
        target.rmdir()  # Controlled empty sandbox only.
        page.set_preview(service.preview_paths(root, [valid]))
        success = executor.execute(root, page.selected_ready_plans(), confirmed=True)
        assert success.status is TransactionStatus.COMPLETED and success.success_count == 1
        page._show_execution_result(success)
        _wait_for_scan_idle(qapp, page)
        assert page.state.completed(0) and page.state.preview_current
    finally:
        page.close()
        database.dispose()


def drop(qapp, page, path):
    page.drop_zone.paths_dropped.emit([path])
    _wait_for_scan_idle(qapp, page)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("entry", ["context", "delete"])
def test_three_way_round_trip_checks_details_and_bulk(qapp, tmp_path, reverse, entry):
    paths = tuple(tmp_path / f"{letter} RJ000001" for letter in "ABC")
    unrelated = tmp_path / "D RJ000002"
    for path in (*paths, unrelated):
        path.mkdir()
    if reverse:
        paths = paths[::-1]
    lookup = FakeLookupService()
    service = OrganizerService(lookup)
    page = OrganizerPage(service)
    page.set_preview(service.preview_paths(tmp_path, [paths[0], unrelated]))
    page.resize(1000, 750)
    page.show()
    qapp.processEvents()
    try:
        page.table.setCurrentIndex(page.preview_model.index(0, 1))
        key = page.state.current_key
        drop(qapp, page, paths[1])
        assert page.state.current_key == key
        assert page.state.highlighted_keys == {key}
        assert page.table.currentIndex().row() == 0
        assert BATCH_CONFLICT_WARNING in page.detail_diagnostics.toPlainText()
        assert page.state.checked(1)  # Unrelated READY remains checked.
        assert not page.state.checked(0) and not page.state.checked(2)
        assert [p.status for p in page.state.plans] == [
            RenamePlanStatus.CONFLICT, RenamePlanStatus.READY, RenamePlanStatus.CONFLICT]
        assert page.preview_model.index(0, 1).data() == "冲突"
        assert page.execute_button.text() == "执行重命名（1 项）"
        page.preview_model.bulk_check("select")
        assert page.state.checked_ready_count == 1
        assert all(not (page.preview_model.flags(page.preview_model.index(i, 0))
                        & Qt.ItemFlag.ItemIsUserCheckable) for i in (0, 2))
        drop(qapp, page, paths[2])
        assert [page.state.plans[i].status for i in (0, 2, 3)] == [
            RenamePlanStatus.CONFLICT] * 3
        assert tuple(p.source_path for p in page.state.plans) == (
            paths[0], unrelated, *paths[1:])
        for row in (2, 2):
            page.table.setCurrentIndex(page.preview_model.index(row, 1))
            if entry == "context":
                page.remove_action.trigger()
            else:
                page.table.setFocus()
                QTest.keyClick(page.table, Qt.Key.Key_Delete)
            if len(page.state.plans) == 3:
                assert page.state.plans[0].status is RenamePlanStatus.CONFLICT
        assert page.state.plans[0].status is RenamePlanStatus.READY
        assert not page.state.checked(0) and page.state.checked(1)
        drop(qapp, page, paths[1])
        assert page.state.plans[0].status is RenamePlanStatus.CONFLICT
        assert len(lookup.calls) == 5  # Only each dropped member is looked up.
        assert set(page.state.review_scope.values()) == {paths[0], unrelated, paths[1]}
    finally:
        page.close()


class NamedLookup(FakeLookupService):
    def __init__(self, names):
        super().__init__()
        self.names = names

    def lookup(self, raw_workno):
        result = super().lookup(raw_workno)
        return replace(result, formatted_name=self.names[raw_workno])


@pytest.mark.parametrize("scenario", [
    "duplicate", "case-alias", "directory", "file", "source", "invalid", "unchanged",
])
def test_append_matches_full_batch_and_preserves_occupancy(qapp, tmp_path, scenario):
    a, b, c = (tmp_path / f"{letter} RJ{i:06}" for i, letter in enumerate("ABC", 1))
    for path in (a, b, c):
        path.mkdir()
    targets = {"RJ000001": "X", "RJ000002": "Y", "RJ000003": "Z"}
    expected = RenamePlanStatus.READY
    if scenario in {"duplicate", "case-alias"}:
        targets.update(RJ000002="x" if scenario == "case-alias" else "X", RJ000003="FOO")
        expected = RenamePlanStatus.CONFLICT
    elif scenario in {"directory", "file"}:
        occupant = tmp_path / "X"
        occupant.mkdir() if scenario == "directory" else occupant.write_text("occupied")
        expected = RenamePlanStatus.CONFLICT
    elif scenario == "source":
        targets["RJ000001"] = b.name
        expected = RenamePlanStatus.CONFLICT
    elif scenario == "invalid":
        targets["RJ000001"] = "../unsafe"
        expected = RenamePlanStatus.INVALID_TARGET
    elif scenario == "unchanged":
        targets["RJ000001"] = a.name
        expected = RenamePlanStatus.UNCHANGED
    service = OrganizerService(NamedLookup(targets))
    page = OrganizerPage(service)
    try:
        for path in (a, b, c):
            drop(qapp, page, path)
        full = service.preview_paths(tmp_path, [a, b, c])
        assert page.state.plans == full.plans
        assert page.state.plans[0].status is expected
        page.state.project_highlight([2], 2)
        page.remove_selected()
        assert page.state.plans[0].status is expected
        if scenario in {"directory", "file", "source"}:
            # Removing B from scope cannot remove its actual disk occupancy.
            page.state.project_highlight([1], 1)
            page.remove_selected()
            assert page.state.plans[0].status is RenamePlanStatus.CONFLICT
    finally:
        page.close()


def test_casefold_three_way_matches_full_scan(qapp, tmp_path):
    paths = [tmp_path / f"{letter} RJ{i:06}" for i, letter in enumerate("ABC", 1)]
    for path in paths:
        path.mkdir()
    service = OrganizerService(NamedLookup(dict(zip(
        (f"RJ{i:06}" for i in range(1, 4)), ("Foo", "foo", "FOO"), strict=True))))
    page = OrganizerPage(service)
    try:
        for path in reversed(paths):
            drop(qapp, page, path)
        assert all(p.status is RenamePlanStatus.CONFLICT for p in page.state.plans)
        full = {p.source_path: p for p in service.preview(tmp_path).plans}
        assert all(p == full[p.source_path] for p in page.state.plans)
    finally:
        page.close()


def test_upsert_preserves_position_and_unblocks_existing_without_checks(qapp, tmp_path):
    paths = [tmp_path / f"{letter} RJ{i:06}" for i, letter in enumerate("ABC", 1)]
    for path in paths:
        path.mkdir()
    lookup = NamedLookup({"RJ000001": "X", "RJ000002": "X", "RJ000003": "Z"})
    service = OrganizerService(lookup)
    page = OrganizerPage(service)
    try:
        drop(qapp, page, paths[0])
        drop(qapp, page, paths[1])
        drop(qapp, page, paths[2])
        assert not page.state.checked(2)  # New READY append is unchecked.
        page.preview_model.setData(page.preview_model.index(2, 0), Qt.CheckState.Checked,
                                   Qt.ItemDataRole.CheckStateRole)
        lookup.names["RJ000002"] = "Y"
        drop(qapp, page, paths[1])
        assert [p.source_path for p in page.state.plans] == paths
        assert all(p.status is RenamePlanStatus.READY for p in page.state.plans)
        assert [page.state.checked(i) for i in range(3)] == [False, False, True]
        drop(qapp, page, paths[2])
        assert page.state.checked(2)  # READY upsert retains old check.
    finally:
        page.close()


@pytest.mark.parametrize("surface", ["table", "empty", "background"])
def test_real_qt_drop_routes_share_batch_reconciliation(qapp, tmp_path, surface):
    paths = [tmp_path / f"{letter} RJ000001" for letter in "AB"]
    for path in paths:
        path.mkdir()
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.resize(1000, 750)
    page.show()
    qapp.processEvents()
    try:
        for path in paths:
            target = {"table": page.table.viewport(), "empty": page.empty_title_label,
                      "background": page}[surface]
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            qapp.sendEvent(target, enter)
            assert enter.isAccepted()
            event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            qapp.sendEvent(target, event)
            assert event.isAccepted()
            _wait_for_scan_idle(qapp, page)
        assert [p.status for p in page.state.plans] == [RenamePlanStatus.CONFLICT] * 2
    finally:
        page.close()


@pytest.mark.parametrize("condition", ["hidden", "settings", "revision", "request"])
def test_append_ownership_accepts_once_or_rejects_before_reconciliation(
    qapp, tmp_path, condition, monkeypatch,
):
    paths = [tmp_path / f"{letter} RJ000001" for letter in "AB"]
    for path in paths:
        path.mkdir()
    service = ControlledOrganizer(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview_paths(tmp_path, paths[:1]))
    old = page.preview
    calls = []
    commit = page.preview_model.append_preview

    def spy(preview):
        calls.append(preview)
        return commit(preview)

    monkeypatch.setattr(page.preview_model, "append_preview", spy)
    service.entered, service.release = Event(), Event()
    page.drop_zone.paths_dropped.emit(paths[1:])
    request, revision = page.state.request_id, page.state.preview_revision
    assert service.entered.wait(2)
    if condition == "settings":
        service.generation += 1
    elif condition == "revision":
        page.invalidate_preview()
    elif condition == "request":
        page.state.request_id += 1
    else:
        page.hide()
    try:
        service.release.set()
        _wait_for_scan_idle(qapp, page)
        if condition == "hidden":
            assert len(calls) == 1
            assert [p.status for p in page.state.plans] == [RenamePlanStatus.CONFLICT] * 2
            page.show()
            qapp.processEvents()
            assert page.preview_model.index(0, 1).data() == "冲突"
        else:
            assert page.preview is old and not calls
        incoming = service.preview_paths(tmp_path, paths[1:])
        page.state.scan_mode = ScanMode.APPEND
        page._show_preview(OrganizerWorkerResult(request, revision, incoming))
        assert len(calls) == (1 if condition == "hidden" else 0)
    finally:
        page.close()


def test_append_preserves_completed_and_warning_policy(qapp, tmp_path):
    paths = [tmp_path / name for name in ("A done RJ000001", "B RJ000002", "C RJ000002")]
    for path in paths:
        path.mkdir()
    service = OrganizerService(FakeLookupService())
    preview = service.preview_paths(tmp_path, paths[:2])
    done = replace(preview.plans[0], status=RenamePlanStatus.UNCHANGED,
                   proposed_name=paths[0].name, target_path=paths[0])
    caution = replace(preview.plans[1], warnings=("metadata caution",))
    page = OrganizerPage(service)
    page.set_preview(replace(preview, plans=(done, caution)))
    page.state.completed_targets.add(page.state.row_keys[0])
    try:
        drop(qapp, page, paths[2])
        assert page.state.completed(0) and page.preview_model.status_label(0) == "已完成"
        assert page.state.plans[1].status is RenamePlanStatus.CONFLICT
        assert "metadata caution" in page.state.plans[1].warnings
        assert "警告" not in page.preview_model.status_label(1)
        page.state.project_highlight([2], 2)
        page.remove_selected()
        assert page.state.plans[1].status is RenamePlanStatus.READY
        assert page.state.plans[1].warnings == ("metadata caution",)
        assert not page.state.checked(1)
    finally:
        page.close()


@pytest.mark.parametrize("status", [RenamePlanStatus.INVALID_TARGET,
    RenamePlanStatus.LOOKUP_FAILED, RenamePlanStatus.AMBIGUOUS_CODE, RenamePlanStatus.UNCHANGED])
def test_append_leaves_unrelated_nonexecutable_status_intact(qapp, tmp_path, status):
    a, b = (tmp_path / name for name in ("A RJ000001", "B RJ000002"))
    a.mkdir()
    b.mkdir()
    service = OrganizerService(FakeLookupService())
    preview = service.preview_paths(tmp_path, [a])
    plan = replace(preview.plans[0], status=status, warnings=("raw diagnostic",))
    if status is RenamePlanStatus.UNCHANGED:
        plan = replace(plan, proposed_name=a.name, target_path=a)
    page = OrganizerPage(service)
    page.set_preview(replace(preview, plans=(plan,)))
    try:
        drop(qapp, page, b)
        assert page.state.plans[0] == plan
        assert not page.state.checked(0) and not page.state.checked(1)
    finally:
        page.close()


def test_combined_reconciliation_runs_once_off_gui_thread(qapp, tmp_path, monkeypatch):
    a, b = (tmp_path / f"{letter} RJ000001" for letter in "AB")
    a.mkdir()
    b.mkdir()
    lookup = FakeLookupService()
    service = OrganizerService(lookup)
    page = OrganizerPage(service)
    page.set_preview(service.preview_paths(tmp_path, [a]))
    original = page.preview
    entered, release = Event(), Event()
    threads = []
    reevaluate = service.reevaluate_preview

    def blocked(preview, **kwargs):
        threads.append(get_ident())
        entered.set()
        assert release.wait(3)
        return reevaluate(preview, **kwargs)

    monkeypatch.setattr(service, "reevaluate_preview", blocked)
    page.drop_zone.paths_dropped.emit([b])
    try:
        _wait_for(qapp, entered.is_set)
        assert page.is_busy() and not page.state.checks_editable
        assert page.preview is original and len(page.state.plans) == 1
        assert threads == [threads[0]] and threads[0] != get_ident()
        release.set()
        _wait_for_scan_idle(qapp, page)
        assert len(threads) == 1 and lookup.calls == ["RJ000001", "RJ000001"]
        assert [p.status for p in page.state.plans] == [RenamePlanStatus.CONFLICT] * 2
    finally:
        release.set()
        _wait_for_scan_idle(qapp, page)
        page.close()


def test_full_scan_append_warning_equivalence(qapp, tmp_path):
    class CautiousLookup(FakeLookupService):
        def lookup(self, raw_workno):
            return replace(super().lookup(raw_workno),
                           freshness=LookupFreshness.CACHE_STALE_FALLBACK)

    paths = [tmp_path / f"{letter} RJ000001" for letter in "ABC"]
    for path in paths:
        path.mkdir()
    service = OrganizerService(CautiousLookup())
    page = OrganizerPage(service)
    try:
        for path in paths:
            drop(qapp, page, path)
        assert page.state.plans == service.preview(tmp_path).plans
        assert all(len(p.warnings) == 2 for p in page.state.plans)
        assert all(page.preview_model.status_label(i) == "冲突" for i in range(3))
    finally:
        page.close()


def test_newly_created_appended_source_occupies_existing_proposal(qapp, tmp_path):
    a, b = (tmp_path / f"{letter} RJ{i:06}" for i, letter in enumerate("AB", 1))
    a.mkdir()
    service = OrganizerService(NamedLookup({"RJ000001": b.name, "RJ000002": "Y"}))
    page = OrganizerPage(service)
    try:
        drop(qapp, page, a)
        assert page.state.plans[0].status is RenamePlanStatus.READY
        b.mkdir()  # The incoming source now occupies the old row's proposed target.
        drop(qapp, page, b)
        assert page.state.plans == service.preview_paths(tmp_path, [a, b]).plans
        assert page.state.plans[0].status is RenamePlanStatus.CONFLICT
        assert not page.state.checked(0)
    finally:
        page.close()
