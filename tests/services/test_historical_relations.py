# ruff: noqa
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from dlsite_organizer.services.historical_relations import HistoricalRelationService
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource


def _observation(identifier, workno, info, when):
    return SimpleNamespace(
        id=identifier,
        workno=workno,
        source="DLSITE_PRODUCT_INFO_AJAX",
        translation_json=info.model_dump_json() if info is not None else None,
        observed_at=when,
    )


class _Store:
    def __init__(self, rows):
        self.rows = tuple(rows)

    def list_all_observations(self):
        return self.rows


def test_parent_derivation_and_reverse_lookup():
    info = TranslationInfoSource(
        original_workno="RJ01609020",
        child_worknos=["RJ01637033", "RJ01636950", "RJ01663275"],
        is_parent=True,
    )
    service = HistoricalRelationService(_Store([_observation(1, "RJ01636949", info, datetime(2026, 1, 1, tzinfo=UTC))]))
    outgoing = service.for_work("RJ01636949").outgoing
    assert {(r.relation_type.value, r.target_workno) for r in outgoing} == {
        ("translation_of", "RJ01609020"),
        ("has_translation_child", "RJ01637033"),
        ("has_translation_child", "RJ01636950"),
        ("has_translation_child", "RJ01663275"),
    }
    incoming = service.for_work("RJ01609020").incoming
    assert incoming[0].subject_workno == "RJ01636949"
    assert incoming[0].confidence.value == "confirmed"


def test_child_derivation_and_no_target_observation_required():
    info = TranslationInfoSource(original_workno="RJ01609020", parent_workno="RJ01636949", is_child=True)
    service = HistoricalRelationService(_Store([_observation(2, "RJ01637033", info, datetime(2026, 1, 2, tzinfo=UTC))]))
    relations = service.for_work("RJ01637033").outgoing
    assert {(r.relation_type.value, r.target_workno) for r in relations} == {
        ("child_of_translation", "RJ01636949"), ("translation_of", "RJ01609020")
    }
    assert service.for_work("RJ01609020").incoming[0].subject_workno == "RJ01637033"


def test_duplicate_aggregation_and_changed_relation_set():
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    parent = lambda children: TranslationInfoSource(original_workno="RJ00000001", child_worknos=children, is_parent=True)
    rows = [_observation(i, "RJ00000002", parent(["RJ00000003", "RJ00000004"] if i == 1 else ["RJ00000003"]), t1 + timedelta(days=i)) for i in (1, 2, 3)]
    service = HistoricalRelationService(_Store(rows))
    rels = {r.target_workno: r for r in service.for_work("RJ00000002").outgoing}
    assert rels["RJ00000004"].observation_count == 1
    assert rels["RJ00000004"].last_seen == t1 + timedelta(days=1)
    assert rels["RJ00000003"].observation_count == 3
    assert rels["RJ00000003"].first_seen == t1 + timedelta(days=1)


def test_malformed_payload_is_skipped():
    valid = TranslationInfoSource(original_workno="RJ00000001", is_parent=True)
    bad = SimpleNamespace(id=1, workno="RJ00000002", source="old", translation_json="{broken", observed_at=datetime.now(UTC))
    good = _observation(2, "RJ00000003", valid, datetime.now(UTC))
    relations = HistoricalRelationService(_Store([bad, good])).for_work("RJ00000003").outgoing
    assert len(relations) == 1
    assert relations[0].target_workno == "RJ00000001"


def test_original_observation_does_not_invent_relations():
    original = TranslationInfoSource(is_original=True)
    assert HistoricalRelationService(_Store([_observation(1, "RJ00000001", original, datetime.now(UTC))])).for_work("RJ00000001").outgoing == ()
