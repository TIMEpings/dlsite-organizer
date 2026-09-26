from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect

from dlsite_organizer.domain.bonus import BonusEvidence, BonusEvidenceSnapshot
from dlsite_organizer.domain.work import Availability, Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.providers.dlsite.client import DlsiteWorkLookup
from dlsite_organizer.providers.dlsite.sources import (
    normalize_product_info_ajax,
    parse_product_info_ajax,
)
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "dlsite"


@dataclass
class SequencedProvider:
    responses: list[DlsiteWorkLookup]
    calls: int = 0

    def fetch_work_lookup(self, workno: str) -> DlsiteWorkLookup:
        del workno
        response = self.responses[self.calls]
        self.calls += 1
        return response


def make_work() -> Work:
    return Work(
        workno="RJ00000001",
        title="作品",
        maker_id="RG00000001",
        maker_name="社团",
        release_date=date(2030, 1, 1),
        availability=Availability.AVAILABLE,
        source_section="maniax",
    )


def make_lookup(
    work: Work,
    *,
    bonuses: object = None,
    include_bonuses: bool = True,
) -> DlsiteWorkLookup:
    values: dict[str, object] = {
        "requested_workno": work.workno,
        "envelope_workno": work.workno,
        "work_name": work.title,
    }
    if include_bonuses:
        values["bonuses"] = bonuses
    from dlsite_organizer.providers.dlsite.sources import ProductInfoAjaxSource

    product = ProductInfoAjaxSource.model_validate(values)
    return DlsiteWorkLookup(work=work, product_info=product, source="DLSITE_PRODUCT_INFO_AJAX")


def make_longitudinal_fixture_lookup(workno: str, phase: str) -> DlsiteWorkLookup:
    payload = (FIXTURE_DIR / f"product_info_{workno}_{phase}.json").read_text(encoding="utf-8")
    product_info = parse_product_info_ajax(payload, workno)
    return DlsiteWorkLookup(
        work=normalize_product_info_ajax(product_info, section="maniax"),
        product_info=product_info,
        source="DLSITE_PRODUCT_INFO_AJAX",
    )


def test_live_observations_preserve_non_empty_then_empty_bonus_states(tmp_path: Path) -> None:
    work = make_work()
    t1 = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 30, 10, 1, tzinfo=UTC)
    provider = SequencedProvider(
        [
            make_lookup(
                work,
                bonuses=[
                    {
                        "title": "Limited purchase bonus",
                        "description": "特典説明 ✨" * 100,
                        "start_date": "2030-01-01",
                        "end_at": "2030-01-10T23:59:59+09:00",
                        "workno": "RJ00000002",
                        "product_id": "RJ00000002",
                        "url": "/maniax/work/=/product_id/RJ00000002.html",
                        "type": "limited",
                        "label": "期間限定",
                    }
                ],
            ),
            make_lookup(work, bonuses=[]),
        ]
    )
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    current_time = [t1]
    service = LookupService(
        provider,
        NamingService(),
        metadata_store=store,
        clock=lambda: current_time[0],
    )

    service.lookup(work.workno)
    current_time[0] = t2
    service.lookup(work.workno, force_refresh=True)

    observations = store.list_bonus_observations(work.workno)

    assert len(observations) == 2
    assert observations[0].evidence is not None
    assert observations[0].evidence.entries == (
        BonusEvidence(
            title="Limited purchase bonus",
            description="特典説明 ✨" * 100,
            start_at=date(2030, 1, 1),
            end_at=datetime(2030, 1, 10, 23, 59, 59, tzinfo=timezone(timedelta(hours=9))),
            workno="RJ00000002",
            product_id="RJ00000002",
            url="/maniax/work/=/product_id/RJ00000002.html",
            bonus_type="limited",
            label="期間限定",
        ),
    )
    assert observations[1].evidence == BonusEvidenceSnapshot(entries=())
    assert provider.calls == 2
    database.dispose()


def test_real_expiry_transition_keeps_historical_positive_evidence(tmp_path: Path) -> None:
    provider = SequencedProvider(
        [
            make_longitudinal_fixture_lookup("RJ01690645", "pre_expiry"),
            make_longitudinal_fixture_lookup("RJ01690645", "post_expiry"),
        ]
    )
    database = Database(tmp_path / "real-bonus-history.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    current_time = [
        datetime(2026, 9, 6, 14, 59, tzinfo=UTC),
        datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
    ]
    service = LookupService(
        provider,
        NamingService(),
        metadata_store=store,
        clock=lambda: current_time[0],
    )

    service.lookup("RJ01690645")
    current_time[0] = current_time[1]
    service.lookup("RJ01690645", force_refresh=True)

    observations = store.list_bonus_observations("RJ01690645")
    assert len(observations) == 2
    assert observations[0].evidence is not None
    assert observations[0].evidence.entries[0].description == "Redacted source description"
    assert observations[0].evidence.entries[0].end_at == datetime(2026, 9, 6, 23, 59, 59)
    assert observations[1].evidence == BonusEvidenceSnapshot(entries=())
    assert provider.calls == 2
    database.dispose()


def test_bonus_entry_order_and_missing_optional_fields_are_stable() -> None:
    payload = {
        "RJ00000001": {
            "work_name": "作品",
            "bonuses": [
                {"title": "A", "description": "日本語"},
                {"name": "B", "body": "中文 😀", "end_date": "2030-02-03"},
                {"title": "C", "product_id": "RJ00000004", "href": "https://example.test/c"},
            ],
        }
    }

    source = parse_product_info_ajax(json.dumps(payload, ensure_ascii=False), "RJ00000001")

    assert source.bonus_evidence is not None
    assert [entry.title for entry in source.bonus_evidence.entries] == ["A", "B", "C"]
    assert source.bonus_evidence.entries[0].description == "日本語"
    assert source.bonus_evidence.entries[0].end_at is None
    assert source.bonus_evidence.entries[1].description == "中文 😀"
    assert source.bonus_evidence.entries[1].end_at == date(2030, 2, 3)
    assert source.bonus_evidence.entries[2].product_id == "RJ00000004"
    assert source.bonus_evidence.entries[2].url == "https://example.test/c"


def test_malformed_optional_bonus_temporal_value_does_not_break_metadata_parse() -> None:
    source = parse_product_info_ajax(
        json.dumps(
            {
                "RJ00000001": {
                    "work_name": "作品",
                    "bonuses": [{"title": "可用标题", "end_at": "not-a-date"}],
                }
            }
        ),
        "RJ00000001",
    )

    assert source.work_name == "作品"
    assert source.bonus_evidence is not None
    assert source.bonus_evidence.entries[0].title == "可用标题"
    assert source.bonus_evidence.entries[0].end_at is None


def test_legacy_metadata_observation_reads_bonus_evidence_as_unknown(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workno VARCHAR(16) NOT NULL,
            title VARCHAR NOT NULL,
            maker_id VARCHAR(32),
            maker_name VARCHAR,
            release_date DATE,
            regist_datetime DATETIME,
            work_type VARCHAR,
            age_category VARCHAR,
            availability VARCHAR(32) NOT NULL,
            source VARCHAR(64) NOT NULL,
            translation_json TEXT,
            observed_at DATETIME NOT NULL
        );
        INSERT INTO metadata_observations
            (workno, title, maker_id, maker_name, availability, source,
             translation_json, observed_at)
        VALUES
            ('RJ00000001', 'legacy', 'RG00000001', '社团', 'available', 'old',
             '{"is_parent": true}', '2030-01-01 00:00:00');
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    store = MetadataStore(database)

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    columns = {column["name"] for column in inspect(engine).get_columns("metadata_observations")}
    observations = store.list_bonus_observations("RJ00000001")

    assert "bonus_evidence_json" in columns
    assert len(observations) == 1
    assert observations[0].evidence is None
    engine.dispose()
    database.dispose()
