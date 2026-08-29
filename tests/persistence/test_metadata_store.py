import json
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect

from dlsite_organizer.domain.work import Availability, Work
from dlsite_organizer.persistence.database import (
    Database,
    RenameOperationRecord,
    RenameTransactionRecord,
    WorkObservation,
)
from dlsite_organizer.persistence.metadata_store import (
    MetadataStore,
    WorkMetadataCache,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource


def make_work(
    workno: str = "RJ01609020",
    *,
    title: str = "作品标题",
    maker_id: str | None = "RG01058997",
    maker_name: str | None = "同名社团",
) -> Work:
    return Work(
        workno=workno,
        title=title,
        maker_id=maker_id,
        maker_name=maker_name,
        release_date=date(2026, 7, 7),
        series_name="系列名",
        cvs=["CV A", "CV B"],
        tags=["标签 A", "标签 B"],
        cover_url="https://img.example.test/cover.jpg",
        availability=Availability.AVAILABLE,
        source_section="maniax",
    )


def make_translation() -> TranslationInfoSource:
    return TranslationInfoSource.model_validate(
        {
            "is_translation_agree": True,
            "is_volunteer": False,
            "is_original": False,
            "is_parent": True,
            "is_child": False,
            "original_workno": "RJ01609020",
            "parent_workno": None,
            "child_worknos": ["RJ01637033", "RJ01636950"],
            "lang": "中文-简体 🌏",
            "unknown_optional_key": {"preserve": True},
        }
    )


def test_metadata_initialization_creates_separate_current_and_history_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "metadata.sqlite3"
    database = Database(path)
    database.initialize()

    before_engine = create_engine(f"sqlite:///{path.as_posix()}")
    before_store = set(inspect(before_engine).get_table_names())
    assert before_store == {"rename_operations", "rename_transactions", "work_observations"}
    before_engine.dispose()

    store = MetadataStore(database)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    assert {"work_metadata_cache", "metadata_observations"} <= set(
        inspect(engine).get_table_names()
    )
    assert inspect(engine).get_pk_constraint("work_metadata_cache")["constrained_columns"] == [
        "workno"
    ]
    assert inspect(engine).get_pk_constraint("metadata_observations")["constrained_columns"] == [
        "id"
    ]
    assert store.get("RJ01609020") is None
    engine.dispose()
    database.dispose()


def test_metadata_store_round_trips_complete_work_and_source_fields_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metadata.sqlite3"
    database = Database(path)
    database.initialize()
    store = MetadataStore(database)
    work = make_work()
    translation = make_translation()
    fetched_at = datetime(2026, 8, 30, 10, 11, 12, 345678, tzinfo=UTC)
    regist_datetime = datetime(
        2026, 7, 7, 12, 34, 56, 789000, tzinfo=timezone(timedelta(hours=8))
    )
    observed_at = datetime(2026, 8, 30, 10, 11, 13, 456789, tzinfo=UTC)

    store.save(
        work,
        translation,
        source="DLSITE_PRODUCT_INFO_AJAX",
        fetched_at=fetched_at,
        regist_datetime=regist_datetime,
    )
    store.append_observation(
        work,
        translation,
        source="DLSITE_PRODUCT_INFO_AJAX",
        observed_at=observed_at,
        work_type="game",
        age_category=18,
        regist_datetime=regist_datetime,
    )
    database.dispose()

    restarted_database = Database(path)
    restarted_database.initialize()
    restarted_store = MetadataStore(restarted_database)
    cached = restarted_store.get("RJ01609020")

    assert cached is not None
    assert cached.work == work
    assert cached.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert cached.fetched_at == fetched_at
    assert cached.fetched_at.tzinfo is UTC
    assert cached.regist_datetime == regist_datetime
    assert cached.regist_datetime is not None and cached.regist_datetime.tzinfo is UTC
    assert cached.translation_info == translation

    observations = restarted_store.list_observations("RJ01609020")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.workno == work.workno
    assert observation.title == work.title
    assert observation.maker_id == work.maker_id
    assert observation.maker_name == work.maker_name
    assert observation.release_date == work.release_date
    assert observation.work_type == "game"
    assert observation.age_category == "18"
    assert observation.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert observation.observed_at == observed_at
    assert observation.observed_at.tzinfo is UTC
    assert observation.regist_datetime == regist_datetime
    assert observation.regist_datetime is not None and observation.regist_datetime.tzinfo is UTC
    assert observation.translation_json is not None
    assert TranslationInfoSource.model_validate(
        json.loads(observation.translation_json)
    ) == translation
    assert "raw_html" not in observation.translation_json
    assert "unknown_optional_key" in observation.translation_json
    restarted_database.dispose()


def test_metadata_observations_are_append_only_preserve_maker_identity_and_are_ordered(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    first_time = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    same_time = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)

    first = make_work(title="A", maker_id="RG-one", maker_name="同名社团")
    second = make_work(title="B", maker_id="RG-two", maker_name="同名社团")
    store.append_observation(first, None, source="DLSITE_HTML_JSONLD", observed_at=first_time)
    store.append_observation(second, None, source="DLSITE_HTML_JSONLD", observed_at=same_time)
    store.append_observation(
        make_work(title="C", maker_id="RG-three", maker_name="同名社团"),
        None,
        source="DLSITE_HTML_JSONLD",
        observed_at=same_time,
    )

    observations = store.list_observations("RJ01609020")

    assert [(row.observed_at, row.id) for row in observations] == [
        (same_time, 2),
        (same_time, 3),
        (first_time, 1),
    ]
    assert [(row.title, row.maker_id, row.maker_name) for row in observations] == [
        ("B", "RG-two", "同名社团"),
        ("C", "RG-three", "同名社团"),
        ("A", "RG-one", "同名社团"),
    ]
    database.dispose()


def test_malformed_cached_translation_payload_is_unusable(tmp_path: Path) -> None:
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    store.save(
        make_work(),
        make_translation(),
        source="DLSITE_PRODUCT_INFO_AJAX",
        fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
    )

    with database.session() as session:
        row = session.get(WorkMetadataCache, "RJ01609020")
        assert row is not None
        row.translation_json = "{not valid json"
        session.commit()

    assert store.get("RJ01609020") is None
    database.dispose()


def test_v041_schema_migration_keeps_rename_journal_rows_and_adds_v05_metadata_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v041.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE work_observations (
            id INTEGER PRIMARY KEY,
            workno VARCHAR(16) NOT NULL,
            title VARCHAR NOT NULL,
            maker_id VARCHAR(32),
            maker_name VARCHAR,
            source_section VARCHAR(32),
            observed_at DATETIME NOT NULL
        );
        CREATE TABLE rename_transactions (
            id VARCHAR(64) PRIMARY KEY,
            root VARCHAR NOT NULL,
            created_at DATETIME NOT NULL,
            completed_at DATETIME,
            status VARCHAR(32) NOT NULL
        );
        CREATE TABLE rename_operations (
            id INTEGER PRIMARY KEY,
            transaction_id VARCHAR(64) NOT NULL,
            sequence INTEGER NOT NULL,
            source_path VARCHAR NOT NULL,
            target_path VARCHAR NOT NULL,
            status VARCHAR(32) NOT NULL,
            error VARCHAR,
            executed_at DATETIME,
            undo_status VARCHAR(32) NOT NULL,
            undo_error VARCHAR,
            undone_at DATETIME,
            UNIQUE(transaction_id, sequence)
        );
        INSERT INTO work_observations
            VALUES (1, 'RJ01609020', 'old observation', 'RG-old', '同名社团', 'maniax',
                    '2026-08-01 00:00:00');
        INSERT INTO rename_transactions
            VALUES ('tx-1', 'C:/works', '2026-08-01 00:00:00', '2026-08-01 00:01:00', 'COMPLETED');
        INSERT INTO rename_operations
            VALUES (1, 'tx-1', 1, 'C:/works/old', 'C:/works/new', 'SUCCESS', NULL,
                    '2026-08-01 00:01:00', 'PENDING', NULL, NULL);
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    MetadataStore(database)

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    table_names = set(inspect(engine).get_table_names())
    assert {"work_observations", "rename_transactions", "rename_operations"} <= table_names
    assert {"work_metadata_cache", "metadata_observations"} <= table_names
    assert {
        "recovery_stage",
        "recovery_error",
        "recovery_sequence",
    } <= {column["name"] for column in inspect(engine).get_columns("rename_transactions")}
    engine.dispose()

    with database.session() as session:
        old_observation = session.get(WorkObservation, 1)
        transaction = session.get(RenameTransactionRecord, "tx-1")
        operation = session.get(RenameOperationRecord, 1)
        assert old_observation is not None and old_observation.title == "old observation"
        assert transaction is not None and transaction.status == "COMPLETED"
        assert operation is not None and operation.status == "SUCCESS"
    database.dispose()
