"""Offline characterization of planning, mutation facts and review ownership."""

from dataclasses import replace

from PySide6.QtCore import Qt
from tests.ui.test_organizer_page import FakeLookupService, qapp  # noqa: F401

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import ExecutionStatus, TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_planner import RenamePlanner
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.state.organizer_state import OrganizerState, ScanMode


def test_finalized_conflicts_are_not_reversible_by_finalize_alone(tmp_path):
    for name in ("a RJ000001", "b RJ000001"):
        (tmp_path / name).mkdir()
    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    assert all(plan.status is RenamePlanStatus.CONFLICT for plan in preview.plans)
    survivor = RenamePlanner().finalize(preview.plans[:1])
    assert survivor[0].status is RenamePlanStatus.CONFLICT
    # The reusable metadata/proposed name is present; no new lookup is necessary.
    plan = survivor[0]
    assert plan.work is not None and plan.work_code and plan.proposed_name
    replanned = RenamePlanner().plan(tmp_path, plan.source_path, plan.work_code,
                                    plan.work, formatted_name=plan.proposed_name)
    assert replanned.status is RenamePlanStatus.READY


def test_execution_exposes_final_journal_target_identity(tmp_path):
    (tmp_path / "old RJ000001").mkdir()
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    try:
        service = OrganizerService(FakeLookupService())
        preview = service.preview(tmp_path)
        result = RenameExecutor(TransactionJournal(database)).execute(
            tmp_path, preview.plans, confirmed=True)
        assert result.status is TransactionStatus.COMPLETED
        assert result.transaction is not None and not result.transaction.unresolved
        assert result.operations == result.transaction.operations
        operation = result.operations[0]
        assert operation.status is ExecutionStatus.SUCCESS
        assert operation.target_path.is_dir() and not operation.source_path.exists()
        refreshed = service.preview(tmp_path)
        assert refreshed.plans[0].source_path == operation.target_path
        assert refreshed.plans[0].status is RenamePlanStatus.UNCHANGED
    finally:
        database.dispose()


def test_replace_append_remove_and_request_boundaries(qapp, tmp_path):
    (tmp_path / "a RJ000001").mkdir()
    (tmp_path / "b RJ000002").mkdir()
    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(preview)
    model.setData(model.index(0, 0), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    keys = state.row_keys
    model.remove_rows([1])
    assert state.row_keys == keys[:1] and not state.checked(0)
    model.append_preview(preview)
    assert state.row_keys == keys and state.checked_ready_count == 0
    state.begin_scan(ScanMode.REPLACE)
    request, revision = state.request_id, state.preview_revision
    assert state.owns_result(request, revision)
    state.begin_scan(ScanMode.REPLACE)
    assert not state.owns_result(request, revision)
    model.replace_preview(replace(preview, settings_generation=1))
    assert state.stale and not state.checks_editable
    model.replace_preview(preview)
    state.finish_operation()
    assert state.checked_ready_count == 2
