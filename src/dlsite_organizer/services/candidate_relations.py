"""Discover explainable relation candidates from local metadata only."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol, cast

from dlsite_organizer.domain.candidate import (
    CandidateEvidence,
    CandidateEvidenceKind,
    CandidateEvidencePolarity,
    CandidateRelation,
    CandidateSearchResult,
    CandidateSearchState,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.manual_review import CandidateReviewOutcome, ManualReviewEvent
from dlsite_organizer.domain.work_code import WorkCodeError, normalize_rjcode
from dlsite_organizer.services.historical_relations import HistoricalRelationService


class KnownWorkRepository(Protocol):
    def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]: ...


class ManualReviewLookup(Protocol):
    def latest_review_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent | None: ...


class CandidateSearchPolicy:
    """Recall policy, deliberately separate from evidence interpretation."""

    def eligible(self, source: KnownWorkSnapshot, target: KnownWorkSnapshot) -> bool:
        return (
            _maker_identity(source) is not None
            and _maker_identity(source) == _maker_identity(target)
            and source.regist_date is not None
            and source.regist_date == target.regist_date
        )


class CandidateEvidenceEvaluator:
    """Produce structured observations without scores or confidence values."""

    def evaluate(
        self, source: KnownWorkSnapshot, target: KnownWorkSnapshot
    ) -> tuple[CandidateEvidence, ...]:
        evidence: list[CandidateEvidence] = []
        maker = _maker_evidence(source, target)
        if maker is not None:
            evidence.append(maker)
        if source.regist_date is not None and source.regist_date == target.regist_date:
            evidence.append(
                CandidateEvidence(
                    kind=CandidateEvidenceKind.SAME_REGIST_DATE,
                    value=source.regist_date.isoformat(),
                    description="Both works have the same registration date.",
                    provenance="local metadata regist_datetime date component",
                )
            )
        distance = rj_numeric_distance(source.workno, target.workno)
        if distance is not None:
            evidence.append(
                CandidateEvidence(
                    kind=CandidateEvidenceKind.RJ_NUMERIC_DISTANCE,
                    value=distance,
                    description=f"RJ numeric distance: {distance}.",
                    provenance="local work numbers",
                )
            )
        return tuple(evidence)


class CandidateRelationService:
    """Find one-hop candidates in the local known-work universe."""

    def __init__(
        self,
        repository: KnownWorkRepository,
        *,
        policy: CandidateSearchPolicy | None = None,
        evaluator: CandidateEvidenceEvaluator | None = None,
        historical_relations: HistoricalRelationService | None = None,
        clock=None,
        max_results: int = 100,
        manual_reviews: ManualReviewLookup | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or CandidateSearchPolicy()
        self._evaluator = evaluator or CandidateEvidenceEvaluator()
        self._historical_relations = historical_relations
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_results = max(1, max_results)
        self._manual_reviews = manual_reviews

    def for_work(self, workno: str) -> CandidateSearchResult:
        evaluated_at = self._now()
        try:
            normalized = normalize_rjcode(workno)
        except WorkCodeError as exc:
            return CandidateSearchResult(
                state=CandidateSearchState.INSUFFICIENT_METADATA,
                reason=str(exc),
                evaluated_at=evaluated_at,
            )
        snapshots = self._repository.list_known_work_summaries()
        source = next((item for item in snapshots if item.workno == normalized), None)
        if source is None or _maker_identity(source) is None:
            return CandidateSearchResult(
                source_work=source,
                state=CandidateSearchState.INSUFFICIENT_METADATA,
                reason="Source work metadata lacks a comparable maker identity.",
                evaluated_at=evaluated_at,
            )

        excluded = self._confirmed_pairs(normalized)
        maker_count = sum(1 for item in snapshots if _same_maker(source, item))
        day_count = sum(
            1
            for item in snapshots
            if _same_maker(source, item) and item.regist_date == source.regist_date
        )
        found: list[CandidateRelation] = []
        for target in snapshots:
            if target.workno == normalized or not self._policy.eligible(source, target):
                continue
            if tuple(sorted((normalized, target.workno))) in excluded:
                continue
            if self._is_manually_related(normalized, target.workno):
                continue
            supporting = self._evaluator.evaluate(source, target)
            context = (
                CandidateEvidence(
                    kind=CandidateEvidenceKind.LOCAL_MAKER_DAY_GROUP_SIZE,
                    value=day_count,
                    polarity=CandidateEvidencePolarity.CONTEXT,
                    description=f"Locally known same-maker same-date works: {day_count}.",
                    provenance="local known-work universe",
                ),
                CandidateEvidence(
                    kind=CandidateEvidenceKind.LOCAL_KNOWN_MAKER_WORK_COUNT,
                    value=maker_count,
                    polarity=CandidateEvidencePolarity.CONTEXT,
                    description=f"Locally known works for this maker identity: {maker_count}.",
                    provenance="local known-work universe",
                ),
            )
            found.append(
                CandidateRelation(
                    source_workno=normalized,
                    target_workno=target.workno,
                    supporting_evidence=supporting,
                    context=context,
                    evaluated_at=evaluated_at,
                    source_snapshot=source,
                    target_snapshot=target,
                )
            )
        found.sort(
            key=lambda item: (
                rj_numeric_distance(normalized, item.target_workno) or 10**18,
                item.target_workno,
            )
        )
        total = len(found)
        truncated = total > self._max_results
        items = tuple(found[: self._max_results])
        return CandidateSearchResult(
            source_work=source,
            candidates=items,
            state=CandidateSearchState.FOUND if items else CandidateSearchState.NONE,
            evaluated_at=evaluated_at,
            truncated=truncated,
            total_candidate_count=total,
        )

    def _confirmed_pairs(self, source_workno: str) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        if self._historical_relations is not None:
            historical = self._historical_relations.for_work(source_workno)
            pairs.update(
                _canonical_pair(item.subject_workno, item.target_workno)
                for item in (*historical.outgoing, *historical.incoming)
            )
        current_pairs = getattr(self._repository, "list_current_relation_pairs", None)
        if callable(current_pairs):
            with suppress(Exception):
                provider = cast(Callable[[], tuple[tuple[str, str], ...]], current_pairs)
                pairs.update(_canonical_pair(left, right) for left, right in provider())
        return pairs

    def _is_manually_related(self, left: str, right: str) -> bool:
        if self._manual_reviews is None:
            return False
        review = self._manual_reviews.latest_review_for_pair(left, right)
        return review is not None and review.outcome is CandidateReviewOutcome.RELATED

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def normalize_maker_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    return normalized or None


def rj_numeric_distance(left: str, right: str) -> int | None:
    try:
        a = int(normalize_rjcode(left)[2:])
        b = int(normalize_rjcode(right)[2:])
    except (TypeError, ValueError, WorkCodeError):
        return None
    return abs(a - b)


def _canonical_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def _maker_identity(snapshot: KnownWorkSnapshot) -> tuple[str, str] | None:
    if snapshot.maker_id:
        return ("id", snapshot.maker_id)
    name = normalize_maker_name(snapshot.maker_name)
    return ("name", name) if name else None


def _same_maker(left: KnownWorkSnapshot, right: KnownWorkSnapshot) -> bool:
    return _maker_identity(left) is not None and _maker_identity(left) == _maker_identity(right)


def _maker_evidence(left: KnownWorkSnapshot, right: KnownWorkSnapshot) -> CandidateEvidence | None:
    identity = _maker_identity(left)
    if identity is None or identity != _maker_identity(right):
        return None
    kind = (
        CandidateEvidenceKind.SAME_MAKER_ID
        if identity[0] == "id"
        else CandidateEvidenceKind.SAME_MAKER_NAME
    )
    label = "maker_id" if identity[0] == "id" else "exact normalized maker name"
    return CandidateEvidence(
        kind=kind,
        value=identity[1],
        description=f"Same {label}.",
        provenance="local metadata",
    )


# Name used by the architecture notes; kept as an alias so callers can use
# either the concise service name or the discovery-oriented name.
CandidateDiscoveryService = CandidateRelationService
