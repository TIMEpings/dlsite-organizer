from datetime import UTC, datetime
from pathlib import Path

import pytest

from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.persistence.database import Database, RenameTransactionRecord
from dlsite_organizer.persistence.rename_journal import TransactionJournal


def test_list_transactions_is_bounded_ordered_and_preserves_legacy_nulls(tmp_path: Path) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    now = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    completed = journal.create_transaction(
        root,
        ((root / "a", root / "a-new"), (root / "b", root / "b-new")),
    )
    journal.mark_operation_success(completed.transaction_id, 1, now)
    journal.mark_operation_success(completed.transaction_id, 2, now)
    journal.mark_transaction_completed(completed.transaction_id, now)

    partial = journal.create_transaction(root, ((root / "c", root / "c-new"),))
    journal.record_execution_failure(
        partial.transaction_id,
        1,
        "target changed before execution",
        TransactionStatus.PARTIAL,
        now,
    )

    unresolved = journal.create_transaction(
        root,
        ((root / "d", root / "d-new"), (root / "e", root / "e-new")),
    )
    journal.mark_operation_success(unresolved.transaction_id, 1, now)
    journal.mark_recovery_required(
        unresolved.transaction_id,
        2,
        "journal update interrupted",
        now,
    )

    # Equal timestamps exercise the stable ID tie-breaker, independently of insert order.
    with database.session() as session:
        for transaction_id in (
            completed.transaction_id,
            partial.transaction_id,
            unresolved.transaction_id,
        ):
            record = session.get(RenameTransactionRecord, transaction_id)
            assert record is not None
            record.created_at = now
        session.commit()

    first_page = journal.list_transactions(limit=2)
    second_page = journal.list_transactions(limit=2, offset=2)
    expected_order = sorted(
        (completed.transaction_id, partial.transaction_id, unresolved.transaction_id),
        reverse=True,
    )
    actual_order = [transaction.transaction_id for transaction in first_page + second_page]
    assert actual_order == expected_order
    assert len(first_page) == 2
    assert len(second_page) == 1

    transactions_by_id = {item.transaction_id: item for item in first_page + second_page}
    completed_summary = transactions_by_id[completed.transaction_id]
    assert completed_summary.status is TransactionStatus.COMPLETED
    assert completed_summary.root == root

    # List rows are headers only; optional legacy fields and operations load on selection.
    completed_record = journal.get_transaction(completed.transaction_id)
    assert [operation.sequence for operation in completed_record.operations] == [1, 2]
    assert completed_record.recovery_stage is None
    assert completed_record.recovery_error is None
    assert completed_record.recovery_sequence is None
    assert completed_record.operations[0].error is None
    assert completed_record.operations[0].undo_error is None

    assert transactions_by_id[partial.transaction_id].status is TransactionStatus.PARTIAL
    unresolved_record = journal.get_transaction(unresolved.transaction_id)
    assert unresolved_record.status is TransactionStatus.RECOVERY_REQUIRED
    assert unresolved_record.recovery_stage == "forward"
    assert unresolved_record.recovery_sequence == 2
    assert unresolved_record.recovery_error == "journal update interrupted"
    assert [operation.sequence for operation in unresolved_record.operations] == [1, 2]
    database.dispose()


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (101, 0), (1, -1)],
)
def test_list_transactions_rejects_unbounded_or_invalid_pages(
    tmp_path: Path,
    limit: int,
    offset: int,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()

    with pytest.raises(ValueError):
        TransactionJournal(database).list_transactions(limit=limit, offset=offset)

    database.dispose()
