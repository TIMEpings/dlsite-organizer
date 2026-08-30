import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, inspect, select

from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
)
from dlsite_organizer.domain.work import Availability, Work
from dlsite_organizer.persistence.database import (
    Base,
    Database,
    RenameOperationRecord,
    RenameTransactionRecord,
    WorkObservation,
)
from dlsite_organizer.persistence.manual_reviews import (
    ManualRelationReviewRecord,
    ManualReviewRepository,
)
from dlsite_organizer.persistence.metadata_store import (
    MetadataObservation,
    MetadataStore,
    WorkMetadataCache,
)
from dlsite_organizer.persistence.rename_journal import UnavailableRenameJournal
from dlsite_organizer.services.manual_reviews import ManualReviewService

WHEN = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 30, 10, 1, tzinfo=UTC)
SNAPSHOT = CandidateEvidenceSnapshot(
    schema_version=1,
    same_maker_id="RG12345678",
    same_maker_name=None,
    same_regist_date="2026-08-30",
    rj_numeric_distance=4,
    local_maker_day_group_size=2,
    local_known_maker_work_count=5,
    candidate_evaluated_at=WHEN,
    supporting_evidence=(
        {
            "kind": "same_maker_id",
            "value": "RG12345678",
            "polarity": "supporting",
            "description": "Same maker_id.",
            "provenance": "local metadata",
        },
    ),
    context=(
        {
            "kind": "local_maker_day_group_size",
            "value": 2,
            "polarity": "context",
            "description": "Locally known same-maker same-date works: 2.",
            "provenance": "local known-work universe",
        },
    ),
)


def event(
    outcome: CandidateReviewOutcome,
    *,
    workno_a: str = "RJ00000001",
    workno_b: str = "RJ00000002",
    relation_type: ManualRelationType | None = None,
    subject_workno: str | None = None,
    target_workno: str | None = None,
    reviewed_at: datetime = WHEN,
    notes: str | None = None,
) -> ManualReviewEvent:
    return ManualReviewEvent(
        workno_a=workno_a,
        workno_b=workno_b,
        outcome=outcome,
        relation_type=relation_type,
        subject_workno=subject_workno,
        target_workno=target_workno,
        notes=notes,
        evidence_snapshot=SNAPSHOT,
        reviewed_at=reviewed_at,
    )


def repository_for(path: Path) -> tuple[Database, ManualReviewRepository]:
    database = Database(path)
    database.initialize()
    MetadataStore(database)
    return database, ManualReviewRepository(database)


def test_fresh_v08_schema_keeps_core_separate_and_creates_manual_review_indexes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "fresh.sqlite3")
    database.initialize()
    engine = create_engine(f"sqlite:///{database.path.as_posix()}")
    assert set(inspect(engine).get_table_names()) == {
        "rename_operations",
        "rename_transactions",
        "work_observations",
    }
    engine.dispose()

    MetadataStore(database)
    engine = create_engine(f"sqlite:///{database.path.as_posix()}")
    assert {
        "rename_operations",
        "rename_transactions",
        "work_observations",
        "work_metadata_cache",
        "metadata_observations",
        "manual_relation_reviews",
    } == set(inspect(engine).get_table_names())
    columns = {item["name"] for item in inspect(engine).get_columns("manual_relation_reviews")}
    assert {
        "id",
        "workno_a",
        "workno_b",
        "outcome",
        "relation_type",
        "subject_workno",
        "target_workno",
        "notes",
        "evidence_snapshot_json",
        "reviewed_at",
        "provenance",
    } <= columns
    index_names = {item["name"] for item in inspect(engine).get_indexes("manual_relation_reviews")}
    assert {"ix_manual_review_pair_reviewed", "ix_manual_review_reviewed_at"} <= index_names
    engine.dispose()
    database.dispose()


def test_append_only_history_latest_and_reverse_pair_queries(tmp_path: Path) -> None:
    database, repository = repository_for(tmp_path / "reviews.sqlite3")
    service = ManualReviewService(repository)

    first = service.submit_review(
        "RJ00000002",
        "RJ00000001",
        CandidateReviewOutcome.UNSURE,
        evidence_snapshot=SNAPSHOT,
        reviewed_at=WHEN,
    )
    second = service.submit_review(
        "RJ00000001",
        "RJ00000002",
        CandidateReviewOutcome.NOT_RELATED,
        evidence_snapshot=SNAPSHOT,
        reviewed_at=LATER,
    )
    third = service.submit_review(
        "RJ00000002",
        "RJ00000001",
        CandidateReviewOutcome.RELATED,
        evidence_snapshot=SNAPSHOT,
        relation_type=ManualRelationType.BONUS_OF,
        subject_workno="RJ00000002",
        target_workno="RJ00000001",
        reviewed_at=datetime(2026, 8, 30, 10, 2, tzinfo=UTC),
    )

    history = repository.history_for_pair("RJ00000001", "RJ00000002")
    assert len(history) == 3
    assert [item.outcome for item in history] == [
        CandidateReviewOutcome.UNSURE,
        CandidateReviewOutcome.NOT_RELATED,
        CandidateReviewOutcome.RELATED,
    ]
    assert repository.history_for_pair("RJ00000002", "RJ00000001") == history
    assert repository.latest_for_pair("RJ00000002", "RJ00000001") == third
    latest = repository.latest_for_pair("RJ00000002", "RJ00000001")
    assert latest is not None
    assert latest.id == third.id
    assert first.id != second.id != third.id
    with database.session() as session:
        assert len(session.scalars(select(ManualRelationReviewRecord)).all()) == 3
    database.dispose()


def test_equal_review_times_use_id_as_stable_latest_tiebreaker(tmp_path: Path) -> None:
    database, repository = repository_for(tmp_path / "same-time.sqlite3")
    repository.append(event(CandidateReviewOutcome.UNSURE))
    second = repository.append(event(CandidateReviewOutcome.NOT_RELATED))

    latest = repository.latest_for_pair("RJ00000001", "RJ00000002")
    assert latest is not None
    assert latest.id == second.id
    database.dispose()


def test_list_reviews_and_reviews_for_work_cover_both_canonical_columns(tmp_path: Path) -> None:
    database, repository = repository_for(tmp_path / "work-queries.sqlite3")
    repository.append(
        event(CandidateReviewOutcome.UNSURE, workno_a="RJ00000001", workno_b="RJ00000002")
    )
    repository.append(
        event(CandidateReviewOutcome.NOT_RELATED, workno_a="RJ00000001", workno_b="RJ00000003")
    )
    repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            workno_a="RJ00000004",
            workno_b="RJ00000001",
            relation_type=ManualRelationType.OTHER,
        )
    )
    repository.append(
        event(CandidateReviewOutcome.UNSURE, workno_a="RJ00000002", workno_b="RJ00000003")
    )

    reviews = repository.reviews_for_work("rj00000001")
    assert {item.workno_a + "/" + item.workno_b for item in reviews} == {
        "RJ00000001/RJ00000002",
        "RJ00000001/RJ00000003",
        "RJ00000001/RJ00000004",
    }
    assert repository.list_reviews()[:2] == reviews[:2]
    database.dispose()


def test_review_persists_snapshot_direction_and_notes_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.sqlite3"
    database, repository = repository_for(path)
    saved = repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            workno_a="RJ00000002",
            workno_b="RJ00000001",
            relation_type=ManualRelationType.BONUS_OF,
            subject_workno="RJ00000002",
            target_workno="RJ00000001",
            notes="some private review note",
        )
    )
    assert saved.evidence_snapshot == SNAPSHOT
    database.dispose()

    restarted, restarted_repository = repository_for(path)
    loaded = restarted_repository.latest_for_pair("RJ00000001", "RJ00000002")
    assert loaded is not None
    assert loaded.id == saved.id
    assert loaded.workno_a == "RJ00000001"
    assert loaded.workno_b == "RJ00000002"
    assert loaded.subject_workno == "RJ00000002"
    assert loaded.target_workno == "RJ00000001"
    assert loaded.relation_type is ManualRelationType.BONUS_OF
    assert loaded.evidence_snapshot == SNAPSHOT
    assert loaded.notes == "some private review note"
    restarted.dispose()


@pytest.mark.parametrize(
    "relation_type",
    [
        ManualRelationType.SAME_SERIES,
        ManualRelationType.SAME_WORK_VARIANT,
        ManualRelationType.SAME_WORK_LANGUAGE_VARIANT,
        ManualRelationType.INCLUDED_IN,
    ],
)
def test_new_relation_types_round_trip_across_restart(
    tmp_path: Path, relation_type: ManualRelationType
) -> None:
    path = tmp_path / f"{relation_type.value}.sqlite3"
    database, repository = repository_for(path)
    directional = relation_type.is_directional
    saved = repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            workno_a="RJ00000002",
            workno_b="RJ00000001",
            relation_type=relation_type,
            subject_workno="RJ00000002" if directional else None,
            target_workno="RJ00000001" if directional else None,
        )
    )
    database.dispose()

    restarted, restarted_repository = repository_for(path)
    loaded = restarted_repository.latest_for_pair("RJ00000001", "RJ00000002")
    assert loaded is not None
    assert loaded.id == saved.id
    assert loaded.relation_type is relation_type
    assert loaded.subject_workno == ("RJ00000002" if directional else None)
    assert loaded.target_workno == ("RJ00000001" if directional else None)
    restarted.dispose()


@pytest.mark.parametrize(
    "relation_type", [ManualRelationType.OTHER, ManualRelationType.BUNDLED_WITH]
)
def test_legacy_symmetric_relation_round_trip_after_restart(
    tmp_path: Path, relation_type: ManualRelationType
) -> None:
    path = tmp_path / f"legacy-{relation_type.value}.sqlite3"
    database, repository = repository_for(path)
    saved = repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            relation_type=relation_type,
        )
    )
    database.dispose()

    restarted, restarted_repository = repository_for(path)
    loaded = restarted_repository.latest_for_pair("RJ00000001", "RJ00000002")
    assert loaded == saved
    assert loaded is not None
    assert loaded.relation_type is relation_type
    assert loaded.subject_workno is None
    assert loaded.target_workno is None
    restarted.dispose()


def test_included_in_reverse_work_query_keeps_event_direction(tmp_path: Path) -> None:
    database, repository = repository_for(tmp_path / "included-in-query.sqlite3")
    saved = repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            workno_a="RJ00000002",
            workno_b="RJ00000001",
            relation_type=ManualRelationType.INCLUDED_IN,
            subject_workno="RJ00000002",
            target_workno="RJ00000001",
        )
    )

    assert repository.reviews_for_work("RJ00000001") == (saved,)
    assert repository.reviews_for_work("RJ00000002") == (saved,)
    database.dispose()


def test_relation_correction_is_append_only_and_latest_keeps_included_direction(
    tmp_path: Path,
) -> None:
    database, repository = repository_for(tmp_path / "relation-correction.sqlite3")
    first = repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            relation_type=ManualRelationType.BUNDLED_WITH,
            reviewed_at=WHEN,
        )
    )
    second = repository.append(
        event(
            CandidateReviewOutcome.RELATED,
            relation_type=ManualRelationType.INCLUDED_IN,
            subject_workno="RJ00000002",
            target_workno="RJ00000001",
            reviewed_at=LATER,
        )
    )

    history = repository.history_for_pair("RJ00000001", "RJ00000002")
    assert history == (first, second)
    latest = repository.latest_for_pair("RJ00000001", "RJ00000002")
    assert latest == second
    assert latest is not None
    assert latest.relation_type is ManualRelationType.INCLUDED_IN
    assert latest.subject_workno == "RJ00000002"
    assert latest.target_workno == "RJ00000001"
    database.dispose()


def test_malformed_snapshot_rows_are_skipped_without_hiding_valid_history(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database, repository = repository_for(tmp_path / "malformed.sqlite3")
    valid = repository.append(event(CandidateReviewOutcome.UNSURE))
    with database.session() as session:
        session.add(
            ManualRelationReviewRecord(
                workno_a="RJ00000001",
                workno_b="RJ00000002",
                outcome="related",
                relation_type="bonus_of",
                subject_workno="RJ00000002",
                target_workno="RJ00000001",
                evidence_snapshot_json='{"schema_version": 1, "unexpected": true}',
                reviewed_at=LATER,
                provenance="MANUAL_USER_REVIEW",
            )
        )
        session.commit()

    caplog.set_level(logging.WARNING)
    assert repository.history_for_pair("RJ00000001", "RJ00000002") == (valid,)
    assert repository.latest_for_pair("RJ00000001", "RJ00000002") == valid
    assert "Skipping malformed manual review row" in caplog.text
    database.dispose()


def test_notes_are_persisted_but_never_written_to_review_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database, repository = repository_for(tmp_path / "private-note.sqlite3")
    private_note = "some private review note"
    caplog.set_level(logging.INFO)
    repository.append(event(CandidateReviewOutcome.NOT_RELATED, notes=private_note))

    latest = repository.latest_for_pair("RJ00000001", "RJ00000002")
    assert latest is not None
    assert latest.notes == private_note
    assert private_note not in caplog.text
    assert "Manual review saved" in caplog.text
    database.dispose()


def test_commit_failure_rolls_back_the_review_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository = repository_for(tmp_path / "commit-failure.sqlite3")
    actual_session = database.session()

    class FailingContext:
        def __enter__(self):
            return actual_session

        def __exit__(self, exc_type, exc_value, traceback):
            actual_session.close()
            return False

    monkeypatch.setattr(database, "session", lambda: FailingContext())

    def fail_commit() -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(actual_session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        repository.append(event(CandidateReviewOutcome.UNSURE))

    monkeypatch.undo()
    assert repository.list_reviews() == ()
    with database.session() as session:
        assert session.scalars(select(RenameTransactionRecord)).all() == []
    database.dispose()


def test_manual_review_store_does_not_require_a_rename_journal(tmp_path: Path) -> None:
    database, repository = repository_for(tmp_path / "journal-isolation.sqlite3")
    unavailable_journal = UnavailableRenameJournal()
    assert unavailable_journal.available is False

    saved = ManualReviewService(repository).submit_review(
        "RJ00000001",
        "RJ00000002",
        CandidateReviewOutcome.UNSURE,
        evidence_snapshot=SNAPSHOT,
    )

    assert repository.latest_for_pair("RJ00000001", "RJ00000002") == saved
    with database.session() as session:
        assert session.scalars(select(RenameTransactionRecord)).all() == []
    database.dispose()


def test_snapshot_serialization_failure_does_not_create_review_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository = repository_for(tmp_path / "serialization-failure.sqlite3")
    monkeypatch.setattr(
        CandidateEvidenceSnapshot,
        "model_dump_json",
        lambda self: (_ for _ in ()).throw(TypeError("simulated serializer failure")),
    )

    with pytest.raises(TypeError, match="simulated serializer failure"):
        repository.append(event(CandidateReviewOutcome.UNSURE))

    monkeypatch.undo()
    assert repository.list_reviews() == ()
    database.dispose()


def test_v07_schema_migration_preserves_rename_and_metadata_rows(tmp_path: Path) -> None:
    path = tmp_path / "v07.sqlite3"
    old = Database(path)
    old.initialize()
    Base.metadata.create_all(
        old._engine,
        tables=cast(Any, [WorkMetadataCache.__table__, MetadataObservation.__table__]),
    )
    work = Work(
        workno="RJ00000001",
        title="Cached title",
        maker_id="RG12345678",
        availability=Availability.AVAILABLE,
    )
    with old.session() as session:
        session.add(
            RenameTransactionRecord(
                id="tx-v07",
                root="C:/works",
                created_at=WHEN,
                completed_at=None,
                status="RECOVERY_REQUIRED",
                recovery_stage="journal_update",
                recovery_error="sample recovery metadata",
                recovery_sequence=2,
            )
        )
        session.add(
            RenameOperationRecord(
                transaction_id="tx-v07",
                sequence=1,
                source_path="C:/works/a",
                target_path="C:/works/b",
                status="DONE",
                error=None,
                executed_at=WHEN,
                undo_status="NOT_ATTEMPTED",
                undo_error=None,
                undone_at=None,
            )
        )
        session.add(
            WorkObservation(
                workno=work.workno,
                title=work.title,
                maker_id=work.maker_id,
                maker_name=work.maker_name,
                source_section="maniax",
                observed_at=WHEN,
            )
        )
        session.add(
            WorkMetadataCache(
                workno=work.workno,
                title=work.title,
                maker_id=work.maker_id,
                maker_name=work.maker_name,
                release_date=work.release_date,
                regist_datetime=WHEN,
                series_name=None,
                cvs_json="[]",
                tags_json="[]",
                cover_url=None,
                availability=Availability.AVAILABLE.value,
                source_section="maniax",
                translation_json=None,
                source="fixture",
                fetched_at=WHEN,
            )
        )
        session.add(
            MetadataObservation(
                workno=work.workno,
                title=work.title,
                maker_id=work.maker_id,
                maker_name=work.maker_name,
                release_date=work.release_date,
                regist_datetime=WHEN,
                work_type="game",
                age_category="18",
                availability=Availability.AVAILABLE.value,
                source="fixture",
                translation_json=None,
                observed_at=WHEN,
            )
        )
        session.commit()
    old.dispose()

    migrated = Database(path)
    migrated.initialize()
    MetadataStore(migrated)
    repository = ManualReviewRepository(migrated)
    with migrated.session() as session:
        transaction = session.get(RenameTransactionRecord, "tx-v07")
        assert transaction is not None
        assert transaction.status == "RECOVERY_REQUIRED"
        assert transaction.recovery_error == "sample recovery metadata"
        assert (
            session.scalar(
                select(RenameOperationRecord).where(
                    RenameOperationRecord.transaction_id == "tx-v07"
                )
            )
            is not None
        )
        assert (
            session.scalar(select(WorkMetadataCache).where(WorkMetadataCache.workno == work.workno))
            is not None
        )
        assert (
            session.scalar(
                select(MetadataObservation).where(MetadataObservation.workno == work.workno)
            )
            is not None
        )
    assert repository.list_reviews() == ()
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    assert "manual_relation_reviews" in inspect(engine).get_table_names()
    engine.dispose()
    migrated.dispose()
