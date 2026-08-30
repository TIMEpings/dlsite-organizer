"""Build a local-only, derived queue of candidate pairs for human review."""
# ruff: noqa: E501,SIM105

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from itertools import combinations
from typing import Any, Protocol, cast

from dlsite_organizer import __version__
from dlsite_organizer.domain.candidate import (
    CandidatePolicyProvenance,
    CandidateQueueFilter,
    CandidateQueueReviewState,
    CandidateRelation,
    CandidateReviewQueueItem,
    CandidateReviewQueueResult,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.manual_review import ManualReviewEvent, canonical_pair
from dlsite_organizer.services.candidate_relations import (
    CandidateEvidenceEvaluator,
    CandidateSearchPolicy,
    maker_identity_key,
    pair_is_eligible,
    rj_numeric_distance,
)


class QueueMetadataRepository(Protocol):
    def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]: ...


class QueueReviews(Protocol):
    def list_manual_reviews(self) -> tuple[ManualReviewEvent, ...]: ...


class CandidateReviewQueueService:
    """Construct queue items from local snapshots without network or persistence."""

    def __init__(
        self,
        repository: QueueMetadataRepository,
        *,
        policy: CandidateSearchPolicy | None = None,
        evaluator: CandidateEvidenceEvaluator | None = None,
        manual_reviews: QueueReviews | None = None,
        historical_relations=None,
        clock=None,
    ) -> None:
        self._repository = repository
        self._policy = policy or CandidateSearchPolicy()
        self._evaluator = evaluator or CandidateEvidenceEvaluator()
        self._manual_reviews = manual_reviews
        self._historical_relations = historical_relations
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def policy_provenance(self) -> CandidatePolicyProvenance:
        descriptor = self._policy.policy_descriptor
        return CandidatePolicyProvenance(
            policy_id=descriptor.policy_id,
            policy_version=descriptor.policy_version,
            application_version=__version__,
        )

    def build(
        self,
        *,
        filter: CandidateQueueFilter = CandidateQueueFilter.UNREVIEWED,
        offset: int = 0,
        limit: int = 100,
        page: int | None = None,
        page_size: int | None = None,
    ) -> CandidateReviewQueueResult:
        """Return a deterministic page; all inputs are local and read-only."""
        generated_at = self._now()
        snapshots = tuple(self._repository.list_known_work_summaries())
        eligible = [item for item in snapshots if self._eligible_metadata(item)]
        excluded = len(snapshots) - len(eligible)
        reviews = self._latest_reviews()
        confirmed = self._confirmed_pairs(snapshots)
        groups: dict[tuple[tuple[str, str], date], list[KnownWorkSnapshot]] = defaultdict(list)
        maker_counts: dict[tuple[str, str], int] = defaultdict(int)
        for item in snapshots:
            identity = maker_identity_key(item)
            if identity is not None:
                maker_counts[identity] += 1
        for item in eligible:
            identity = maker_identity_key(item)
            # identity and date are guaranteed by _eligible_metadata.
            assert identity is not None and item.regist_date is not None
            groups[(identity, item.regist_date)].append(item)
        items: list[CandidateReviewQueueItem] = []
        state_counts = {state: 0 for state in CandidateQueueReviewState}
        for (identity, regist_date), members in groups.items():
            members.sort(key=lambda item: item.workno)
            maker_count = maker_counts[identity]
            for left, right in combinations(members, 2):
                if not pair_is_eligible(left, right, self._policy):
                    continue
                pair = canonical_pair(left.workno, right.workno)
                if pair in confirmed:
                    continue
                relation = CandidateRelation(
                    source_workno=pair[0],
                    target_workno=pair[1],
                    supporting_evidence=self._evaluator.evaluate(left, right),
                    context=(),
                    evaluated_at=generated_at,
                    source_snapshot=left,
                    target_snapshot=right,
                    policy_provenance=self.policy_provenance,
                )
                context = (
                    self._context("local_maker_day_group_size", len(members)),
                    self._context("local_known_maker_work_count", maker_count),
                )
                review, event_count = reviews.get(pair, (None, 0))
                state = (
                    CandidateQueueReviewState.UNREVIEWED
                    if review is None
                    else CandidateQueueReviewState(review.outcome.value)
                )
                state_counts[state] += 1
                if not self._matches_filter(state, filter):
                    continue
                maker_display = left.maker_name or right.maker_name or identity[1]
                maker_identity = f"{identity[0].upper()}:{identity[1]}"
                distance = rj_numeric_distance(*pair)
                items.append(
                    CandidateReviewQueueItem(
                        workno_a=pair[0],
                        workno_b=pair[1],
                        canonical_pair=pair,
                        work_a_title=left.title if left.workno == pair[0] else right.title,
                        work_b_title=right.title if right.workno == pair[1] else left.title,
                        maker_identity=maker_identity,
                        maker_display=maker_display,
                        regist_date=regist_date,
                        rj_numeric_distance=distance,
                        supporting_evidence=relation.supporting_evidence,
                        context=context,
                        policy_provenance=self.policy_provenance,
                        latest_manual_review=review,
                        review_event_count=event_count,
                        source_snapshot=left if left.workno == pair[0] else right,
                        target_snapshot=right if right.workno == pair[1] else left,
                    )
                )
        items.sort(key=self._ordering)
        total = len(items)
        if page is not None:
            effective_size = max(1, page_size or 100)
            offset = max(0, page - 1) * effective_size
            limit = effective_size
        offset = max(0, offset)
        limit = max(1, limit)
        page_items = tuple(items[offset : offset + limit])
        return CandidateReviewQueueResult(
            policy_provenance=self.policy_provenance,
            filter=CandidateQueueFilter(filter),
            items=page_items,
            total_count=total,
            returned_count=len(page_items),
            generated_at=generated_at,
            known_work_count=len(snapshots),
            eligible_work_count=len(eligible),
            excluded_insufficient_metadata_count=excluded,
            unreviewed_count=state_counts[CandidateQueueReviewState.UNREVIEWED],
            related_count=state_counts[CandidateQueueReviewState.RELATED],
            not_related_count=state_counts[CandidateQueueReviewState.NOT_RELATED],
            unsure_count=state_counts[CandidateQueueReviewState.UNSURE],
        )

    generate = build
    list_queue = build
    build_queue = build
    generate_queue = build

    @staticmethod
    def _eligible_metadata(item: KnownWorkSnapshot) -> bool:
        return maker_identity_key(item) is not None and item.regist_date is not None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _latest_reviews(self) -> dict[tuple[str, str], tuple[ManualReviewEvent, int]]:
        if self._manual_reviews is None:
            return {}
        getter = getattr(self._manual_reviews, "list_manual_reviews", None)
        if not callable(getter):
            getter = getattr(self._manual_reviews, "list_reviews", None)
        if not callable(getter):
            return {}
        latest: dict[tuple[str, str], tuple[ManualReviewEvent, int]] = {}
        review_events = cast(Callable[[], tuple[ManualReviewEvent, ...]], getter)()
        for event in review_events:
            pair = canonical_pair(event.workno_a, event.workno_b)
            previous = latest.get(pair)
            count = (previous[1] if previous else 0) + 1
            if previous is None or (event.reviewed_at, event.id or -1) >= (
                previous[0].reviewed_at,
                previous[0].id or -1,
            ):
                latest[pair] = (event, count)
            else:
                latest[pair] = (previous[0], count)
        return latest

    def _confirmed_pairs(
        self, snapshots: tuple[KnownWorkSnapshot, ...] = ()
    ) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        getter = getattr(self._repository, "list_current_relation_pairs", None)
        if callable(getter):
            try:
                pairs.update(canonical_pair(a, b) for a, b in cast(tuple[tuple[str, str], ...], getter()))
            except Exception:
                pass
        if self._historical_relations is not None:
            getter = getattr(self._historical_relations, "known_confirmed_pair_set", None)
            if callable(getter):
                try:
                    pairs.update(cast(set[tuple[str, str]], getter()))
                except Exception:
                    pass
            elif snapshots and callable(getattr(self._historical_relations, "for_work", None)):
                try:
                    for snapshot in snapshots:
                        facts = self._historical_relations.for_work(snapshot.workno)
                        for relation in (*facts.outgoing, *facts.incoming):
                            pairs.add(canonical_pair(relation.subject_workno, relation.target_workno))
                except Exception:
                    pass
        elif callable(getattr(self._repository, "list_all_observations", None)):
            # Keep the batch path available for lightweight callers that did
            # not explicitly compose HistoricalRelationService.
            from dlsite_organizer.services.historical_relations import HistoricalRelationService

            try:
                pairs.update(HistoricalRelationService(cast(Any, self._repository)).known_confirmed_pair_set())
            except Exception:
                pass
        return pairs

    @staticmethod
    def _matches_filter(state: CandidateQueueReviewState, filter: CandidateQueueFilter) -> bool:
        if filter is CandidateQueueFilter.ALL:
            return True
        if filter is CandidateQueueFilter.REVIEWED:
            return state is not CandidateQueueReviewState.UNREVIEWED
        return state.value == filter.value

    @staticmethod
    def _ordering(item: CandidateReviewQueueItem) -> tuple:
        return (
            -item.regist_date.toordinal(),
            item.maker_display.casefold(),
            item.rj_numeric_distance if item.rj_numeric_distance is not None else 10**18,
            item.workno_a,
            item.workno_b,
        )

    @staticmethod
    def _context(kind: str, value: int):
        from dlsite_organizer.domain.candidate import (
            CandidateEvidence,
            CandidateEvidenceKind,
            CandidateEvidencePolarity,
        )

        enum_kind = CandidateEvidenceKind(kind)
        label = (
            f"Locally known same-maker same-date works: {value}."
            if kind == "local_maker_day_group_size"
            else f"Locally known works for this maker identity: {value}."
        )
        return CandidateEvidence(
            kind=enum_kind,
            value=value,
            polarity=CandidateEvidencePolarity.CONTEXT,
            description=label,
            provenance="local known-work universe",
        )


# Concise alias used by callers and documentation.
CandidateQueueService = CandidateReviewQueueService
