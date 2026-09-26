from pathlib import Path

import pytest

from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_history import (
    CurrentPathState,
    RenameHistoryService,
)


def test_observations_classify_paths_without_inferencing_identity(tmp_path: Path) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    source_directory = root / "source-directory"
    source_directory.mkdir()
    target_file = root / "target-file"
    target_file.write_text("test", encoding="utf-8")
    transaction = journal.create_transaction(
        root,
        (
            (source_directory, root / "absent-target"),
            (root / "absent-source", target_file),
        ),
    )
    journal.mark_recovery_required(
        transaction.transaction_id,
        1,
        "recovery check needed",
        transaction.created_at,
    )

    history = RenameHistoryService(journal)
    selected = history.get_transaction(transaction.transaction_id)
    observations = history.observe_paths(selected)

    assert selected.status is TransactionStatus.RECOVERY_REQUIRED
    assert observations[0].source.state is CurrentPathState.DIRECTORY
    assert observations[0].target.state is CurrentPathState.ABSENT
    assert observations[1].source.state is CurrentPathState.ABSENT
    assert observations[1].target.state is CurrentPathState.FILE
    assert observations[0].target.path == root / "absent-target"
    assert source_directory.is_dir()
    assert target_file.is_file()
    database.dispose()


def test_observation_reports_link_like_path_when_supported(tmp_path: Path) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    target = root / "target"
    target.mkdir()
    link = root / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        database.dispose()
        pytest.skip(f"symlinks are unavailable: {exc}")
    transaction = journal.create_transaction(root, ((link, root / "renamed"),))

    observations = RenameHistoryService(journal).observe_paths(transaction)

    assert observations[0].source.state is CurrentPathState.LINK_LIKE
    database.dispose()


def test_observation_error_is_separate_and_does_not_hide_journal_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = journal.create_transaction(root, ((root / "source", root / "target"),))
    journal.mark_recovery_required(
        transaction.transaction_id,
        None,
        "filesystem and journal may differ",
        transaction.created_at,
    )

    def fail_inspection(_path: Path):
        raise PermissionError("access denied")

    history = RenameHistoryService(journal, path_observer=fail_inspection)
    facts = history.get_transaction(transaction.transaction_id)
    observations = history.observe_paths(facts)

    assert facts.recovery_error == "filesystem and journal may differ"
    assert observations[0].source.state is CurrentPathState.ERROR
    assert observations[0].target.state is CurrentPathState.ERROR
    assert observations[0].source.error == "access denied"
    database.dispose()
