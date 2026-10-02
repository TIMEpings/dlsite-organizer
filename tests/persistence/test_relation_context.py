"""Bounded relation reads agree with the original full-universe repository APIs."""

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import event, insert
from sqlalchemy.orm import Session
from tests.services.test_relation_lookup_characterization import (
    DAY,
    NOW,
    TARGET,
    OfflineProvider,
    make_work,
    populate,
    service_for,
)

from dlsite_organizer.domain.candidate import KnownWorkSnapshot
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import (
    MetadataObservation,
    MetadataStore,
    WorkMetadataCache,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.candidate_relations import (
    CandidateRelationService,
    CandidateSearchPolicy,
)
from dlsite_organizer.services.historical_relations import HistoricalRelationService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService


class FullUniverseStore:
    """Exercise the unchanged legacy APIs, without targeted capabilities."""

    def __init__(self, store: MetadataStore):
        self.store = store

    def get(self, workno):
        return self.store.get(workno)

    def list_all_observations(self):
        return self.store.list_all_observations()

    def list_known_work_summaries(self):
        return self.store.list_known_work_summaries()

    def list_current_relation_pairs(self):
        return self.store.list_current_relation_pairs()


@pytest.fixture
def store(tmp_path: Path):
    database = Database(tmp_path / "context.sqlite3")
    database.initialize()
    repository = MetadataStore(database)
    yield repository
    database.dispose()


def full_universe_service(store: MetadataStore) -> LookupService:
    legacy = FullUniverseStore(store)
    history = HistoricalRelationService(legacy)
    # Only runtime duck typing is needed by Lookup's existing repository contract.
    return LookupService(
        OfflineProvider(),
        NamingService(),
        metadata_store=legacy,  # type: ignore[arg-type]
        historical_relations=history,
        candidate_relations=CandidateRelationService(
            legacy, historical_relations=history, clock=lambda: NOW
        ),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize("size", [100, 1000, 5000])
def test_fresh_lookup_never_materializes_unrelated_library(
    store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    populate(store, 20, True)
    rows = [
        dict(
            workno=f"RJ{number:08}",
            title="無関係",
            maker_id=f"M{number}",
            source="fixture",
            fetched_at=NOW,
            observed_at=NOW,
        )
        for number in range(21, size + 1)
    ]
    with store._database.session() as session:
        session.execute(insert(WorkMetadataCache), rows)
        session.execute(insert(MetadataObservation), rows)
        session.commit()
    service = service_for(store)
    counts: Counter[str] = Counter()
    full_reads = []
    for name in (
        "list_all_observations",
        "list_known_work_summaries",
        "list_current_relation_pairs",
    ):
        spy = Mock(side_effect=AssertionError(f"Unexpected whole-library API: {name}"))
        monkeypatch.setattr(store, name, spy)
        full_reads.append(spy)
    history = service._historical_relations
    assert history is not None
    history_spy = Mock(wraps=history.for_work)
    monkeypatch.setattr(history, "for_work", history_spy)

    def loaded(_session, obj):
        counts[type(obj).__name__] += 1

    def query(*_args):
        counts["sql"] += 1

    event.listen(Session, "loaded_as_persistent", loaded)
    event.listen(store._database._engine, "before_cursor_execute", query)
    try:
        result = service.lookup(TARGET)
    finally:
        event.remove(Session, "loaded_as_persistent", loaded)
        event.remove(store._database._engine, "before_cursor_execute", query)
    assert all(spy.call_count == 0 for spy in full_reads)
    assert history_spy.call_count == 1
    assert counts == {"WorkMetadataCache": 16, "MetadataObservation": 17, "sql": 7}
    assert result.historical_relations.outgoing
    assert len(result.candidates) == 9
    assert len(store.list_relation_observations(TARGET)) == 3


@pytest.mark.parametrize("related", [False, True])
def test_complete_lookup_matches_full_universe_results(store: MetadataStore, related: bool) -> None:
    populate(store, 40, related)
    assert service_for(store).lookup(TARGET) == full_universe_service(store).lookup(TARGET)


@pytest.mark.parametrize("field", ["original_workno", "parent_workno", "child_worknos"])
@pytest.mark.parametrize("code", ["RJ00000010", " rj00000010\t", "\u2003rj00000010\n"])
def test_decoded_incoming_reference_filter_preserves_legacy_json(
    store: MetadataStore,
    field: str,
    code: str,
) -> None:
    populate(store, 20, False)
    info = {
        "is_child": field == "parent_workno",
        "is_parent": field != "parent_workno",
        field: [code, code] if field == "child_worknos" else code,
    }
    # Escape the prefix/digits too: filtering serialized text would miss this.
    raw = (
        json.dumps(info)
        .replace("rj00000010", r"\u0072\u006a00000010")
        .replace("RJ00000010", r"\u0052\u004a00000010")
    )
    with store._database.session() as session:
        for model in (WorkMetadataCache, MetadataObservation):
            row = session.query(model).filter_by(workno="RJ00000011").one()
            row.translation_json = raw
        session.commit()
    target = service_for(store).lookup(TARGET)
    assert target == full_universe_service(store).lookup(TARGET)
    assert len(target.historical_relations.incoming) == 1
    assert store.list_current_relation_pairs_for_work(TARGET) == ((TARGET, "RJ00000011"),)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{broken",
        '{"is_parent": true}',
        '{"original_workno":"RJ00000010"}',
        '{"is_child":true,"is_parent":true,"parent_workno":"RJ00000010"}',
    ],
)
def test_corrupt_unknown_and_invalid_incoming_evidence_agrees_with_legacy(
    store: MetadataStore,
    raw: str | None,
) -> None:
    populate(store, 20, False)
    with store._database.session() as session:
        for model in (WorkMetadataCache, MetadataObservation):
            row = session.query(model).filter_by(workno="RJ00000011").one()
            row.translation_json = raw
        session.commit()
    assert service_for(store).lookup(TARGET) == full_universe_service(store).lookup(TARGET)
    assert store.list_current_relation_pairs_for_work(TARGET) == ()


def test_maker_context_matches_complete_snapshots_after_identity_changes(
    store: MetadataStore,
) -> None:
    for number in range(10, 17):
        store.append_observation(
            make_work(number, maker_id=None, name="Ａ夢 中文"),
            None,
            source="history",
            observed_at=NOW,
        )
    store.save(make_work(10, maker_id=None, name="A夢 中文"), None, source="cache", fetched_at=NOW)
    store.save(make_work(11, maker_id="other"), None, source="cache", fetched_at=NOW)
    store.append_observation(
        make_work(12, maker_id="other"),
        None,
        source="history",
        observed_at=NOW + timedelta(seconds=1),
    )
    store.append_observation(
        make_work(13, maker_id="other"), None, source="history", observed_at=NOW
    )
    store.append_observation(
        make_work(14, maker_id=None, name=" A夢 中文 "), None, source="history", observed_at=NOW
    )
    store.append_observation(
        make_work(15, maker_id=None, name="A夢 中文").model_copy(
            update={"regist_datetime": DAY + timedelta(days=1)}
        ),
        None,
        source="history",
        observed_at=NOW,
    )
    assert service_for(store).lookup(TARGET) == full_universe_service(store).lookup(TARGET)
    assert [s.workno for s in store.list_relation_candidate_summaries(TARGET)] == [
        TARGET,
        "RJ00000014",
        "RJ00000015",
        "RJ00000016",
    ]


def test_large_related_group_keeps_total_counts_and_existing_truncation(
    store: MetadataStore,
) -> None:
    for number in range(1, 132):
        store.save(make_work(number), None, source="fixture", fetched_at=NOW)
    targeted = service_for(store).lookup(TARGET)
    assert targeted == full_universe_service(store).lookup(TARGET)
    assert targeted.candidate_relations is not None
    assert targeted.candidate_relations.total_candidate_count == 130
    assert targeted.candidate_relations.truncated
    assert len(targeted.candidates) == 100
    assert len(store.list_relation_candidate_summaries(TARGET)) == 131


def test_subsequent_requests_observe_new_current_and_historical_evidence(
    store: MetadataStore,
) -> None:
    populate(store, 20, True)
    service = service_for(store)
    initial = service.lookup(TARGET)
    store.save(make_work(10), None, source="changed", fetched_at=NOW)
    store.append_observation(
        make_work(11),
        TranslationInfoSource(is_parent=True, original_workno="RJ00000009"),
        source="changed",
        observed_at=NOW,
    )
    store.append_observation(
        make_work(10),
        TranslationInfoSource(is_parent=True, original_workno="RJ00000008"),
        source="changed",
        observed_at=NOW,
    )
    changed = service.lookup(TARGET)
    assert changed.relations == ()
    assert changed.historical_relations != initial.historical_relations
    assert any(r.target_workno == "RJ00000008" for r in changed.historical_relations.outgoing)
    assert changed == full_universe_service(store).lookup(TARGET)


def test_large_explicit_relation_group_is_not_capped(store: MetadataStore) -> None:
    children = [f"RJ{number:08}" for number in range(11, 152)]
    info = TranslationInfoSource(
        is_parent=True, original_workno="RJ00000001", child_worknos=children
    )
    store.save(make_work(10), info, source="fixture", fetched_at=NOW)
    store.append_observation(make_work(10), info, source="fixture", observed_at=NOW)
    result = service_for(store).lookup(TARGET)
    assert result == full_universe_service(store).lookup(TARGET)
    assert len(result.relations) == len(result.historical_relations.outgoing) == 142
    assert len(store.list_current_relation_pairs_for_work(TARGET)) == 142


def test_custom_candidate_policy_keeps_unrestricted_repository_contract(
    store: MetadataStore,
) -> None:
    class AnyMakerPolicy(CandidateSearchPolicy):
        def eligible(self, source: KnownWorkSnapshot, target: KnownWorkSnapshot) -> bool:
            return source.workno != target.workno

    populate(store, 20, False)
    policy = AnyMakerPolicy()
    targeted = CandidateRelationService(store, policy=policy, clock=lambda: NOW).for_work(TARGET)
    legacy = CandidateRelationService(
        FullUniverseStore(store), policy=policy, clock=lambda: NOW
    ).for_work(TARGET)
    assert targeted == legacy
    assert len(targeted.candidates) == 19


def test_repository_errors_keep_existing_logged_empty_contract(
    store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failed_session():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store._database, "session", failed_session)
    assert store.list_all_observations() == store.list_relation_observations(TARGET) == ()
    assert (
        store.list_current_relation_pairs()
        == store.list_current_relation_pairs_for_work(TARGET)
        == ()
    )
    assert (
        store.list_known_work_summaries() == store.list_relation_candidate_summaries(TARGET) == ()
    )
    assert "database unavailable" in caplog.text
    assert "Relation observations read failed" in caplog.text
    assert "Current relation read failed" in caplog.text


def test_unhandled_historical_service_failure_is_not_converted_to_no_relations(
    store: MetadataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    populate(store, 20, False)
    service = service_for(store)
    history = service._historical_relations
    assert history is not None
    monkeypatch.setattr(history, "for_work", Mock(side_effect=RuntimeError("relation failure")))
    with pytest.raises(RuntimeError, match="relation failure"):
        service.lookup(TARGET)
