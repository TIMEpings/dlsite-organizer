from datetime import UTC, datetime

from dlsite_organizer.domain.candidate import (
    CandidateQueueFilter,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
)
from dlsite_organizer.services.candidate_review_queue import CandidateReviewQueueService

WHEN = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def snapshot(code, maker_id: str | None = "M", maker_name=None, day=1, regist=True):
    return KnownWorkSnapshot(
        workno=code,
        title=f"Title {code}",
        maker_id=maker_id,
        maker_name=maker_name,
        regist_datetime=datetime(2026, 1, day, tzinfo=UTC) if regist else None,
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )


class Repository:
    def __init__(self, items, current_pairs=()):
        self.items = tuple(items)
        self.current_pairs = tuple(current_pairs)
        self.known_calls = 0

    def list_known_work_summaries(self):
        self.known_calls += 1
        return self.items

    def list_current_relation_pairs(self):
        return self.current_pairs


class Reviews:
    def __init__(self, events=()):
        self.events = list(events)

    def list_manual_reviews(self):
        return tuple(self.events)


def event(outcome, a="RJ00000001", b="RJ00000002", reviewed_at=WHEN, id=None):
    return ManualReviewEvent(
        id=id,
        workno_a=a,
        workno_b=b,
        outcome=outcome,
        relation_type=(
            ManualRelationType.BONUS_OF
            if outcome is CandidateReviewOutcome.RELATED
            else None
        ),
        subject_workno=a if outcome is CandidateReviewOutcome.RELATED else None,
        target_workno=b if outcome is CandidateReviewOutcome.RELATED else None,
        evidence_snapshot=CandidateEvidenceSnapshot(),
        reviewed_at=reviewed_at,
    )


def test_groups_make_canonical_pairs_and_context():
    repo = Repository(
        [
            snapshot("RJ00000001"),
            snapshot("RJ00000002"),
            snapshot("RJ00000003"),
            snapshot("RJ00000004", day=2),
            snapshot("RJ00000005", maker_id="N"),
            snapshot("RJ00000006", maker_id=None, maker_name="Circle A", regist=False),
        ]
    )
    result = CandidateReviewQueueService(repo, clock=lambda: WHEN).build(
        filter=CandidateQueueFilter.ALL
    )
    assert [item.canonical_pair for item in result.items] == [
        ("RJ00000001", "RJ00000002"),
        ("RJ00000002", "RJ00000003"),
        ("RJ00000001", "RJ00000003"),
    ]
    assert result.known_work_count == 6
    assert result.eligible_work_count == 5
    assert result.excluded_insufficient_metadata_count == 1
    assert result.items[0].rj_numeric_distance == 1
    assert {entry.value for entry in result.items[0].context} == {3, 4}
    assert result.items[0].maker_identity == "ID:M"
    assert result.policy_provenance.application_version == "0.10.0"
    assert result.unreviewed_count == 3
    assert result.related_count == 0
    assert result.not_related_count == 0
    assert result.unsure_count == 0


def test_identity_semantics_match_candidate_policy():
    repo = Repository(
        [
            snapshot("RJ00000001", "A", "Circle"),
            snapshot("RJ00000002", "B", "Circle"),
            snapshot("RJ00000003", None, " Circle Ａ "),
            snapshot("RJ00000004", None, "Circle A"),
            snapshot("RJ00000005", None, "Circle B"),
        ]
    )
    pairs = {
        item.canonical_pair
        for item in CandidateReviewQueueService(repo).build(filter=CandidateQueueFilter.ALL).items
    }
    assert ("RJ00000001", "RJ00000002") not in pairs
    assert ("RJ00000003", "RJ00000004") in pairs
    assert ("RJ00000003", "RJ00000005") not in pairs


def test_review_filters_latest_state_and_event_count():
    reviews = Reviews(
        [
            event(CandidateReviewOutcome.UNSURE),
            event(CandidateReviewOutcome.RELATED, reviewed_at=WHEN.replace(minute=1), id=2),
            event(CandidateReviewOutcome.NOT_RELATED, reviewed_at=WHEN.replace(minute=2), id=3),
        ]
    )
    service = CandidateReviewQueueService(
        Repository([snapshot("RJ00000001"), snapshot("RJ00000002"), snapshot("RJ00000003")]),
        manual_reviews=reviews,
    )
    all_items = service.build(filter=CandidateQueueFilter.ALL).items
    assert all_items[0].review_state.value == "not_related"
    assert all_items[0].review_event_count == 3
    assert service.build(filter=CandidateQueueFilter.UNREVIEWED).total_count == 2
    assert service.build(filter=CandidateQueueFilter.UNSURE).total_count == 0
    assert service.build(filter=CandidateQueueFilter.REVIEWED).total_count == 1


def test_pagination_and_confirmed_exclusion_are_deterministic():
    items = [snapshot(f"RJ0000{i:04d}") for i in range(1, 24)]
    repo = Repository(items, current_pairs=(("RJ00000001", "RJ00000002"),))
    service = CandidateReviewQueueService(repo)
    first = service.build(filter=CandidateQueueFilter.ALL, page=1, page_size=10)
    second = service.build(filter=CandidateQueueFilter.ALL, page=2, page_size=10)
    assert first.total_count == 252
    assert first.returned_count == 10
    assert second.returned_count == 10
    assert not any(
        item.canonical_pair == ("RJ00000001", "RJ00000002")
        for item in first.items + second.items
    )
    assert len({item.canonical_pair for item in first.items + second.items}) == 20
    assert service.build(filter=CandidateQueueFilter.ALL, offset=250, limit=10).returned_count == 2


def test_queue_reads_repository_once_and_never_provider():
    repo = Repository([snapshot("RJ00000001"), snapshot("RJ00000002")])
    service = CandidateReviewQueueService(repo)
    result = service.build()
    assert repo.known_calls == 1
    assert result.returned_count == 1
