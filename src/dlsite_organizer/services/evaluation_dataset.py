"""Build and export a derived dataset from manual review history."""

from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC
from pathlib import Path
from typing import Protocol, cast

from dlsite_organizer.domain.evaluation import (
    EvaluationExportRecord,
    EvaluationMetricState,
    EvaluationRecord,
    EvaluationRecordState,
    EvaluationSummary,
    EvidenceGroupSummary,
    InvalidSnapshotReview,
    PolicyDistributionSummary,
    RelationTypeCount,
)
from dlsite_organizer.domain.manual_review import (
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    canonical_pair,
)

MINIMUM_DECIDED_LABELS = 20
EVALUATION_POLICY_VERSION = 1
RJ_DISTANCE_THRESHOLDS = (1, 3, 5, 10, 20, 50, 100)


class EvaluationReviewStore(Protocol):
    def list_reviews(self) -> tuple[ManualReviewEvent, ...]: ...


class EvaluationDatasetService:
    """Application-level latest-review selection and simple descriptive stats."""

    def __init__(
        self,
        repository: EvaluationReviewStore,
        *,
        minimum_decided_labels: int = MINIMUM_DECIDED_LABELS,
    ) -> None:
        if minimum_decided_labels < 0:
            raise ValueError("minimum_decided_labels must be non-negative")
        self._repository = repository
        self._minimum_decided_labels = minimum_decided_labels

    def _read_events(self) -> tuple[ManualReviewEvent | InvalidSnapshotReview, ...]:
        getter = getattr(self._repository, "list_reviews_for_evaluation", None)
        if callable(getter):
            evaluation_getter = cast(
                Callable[[], Sequence[ManualReviewEvent | InvalidSnapshotReview]], getter
            )
            return tuple(evaluation_getter())
        return tuple(self._repository.list_reviews())

    def records(self) -> tuple[EvaluationRecord, ...]:
        return self._latest_records(self._read_events())

    @staticmethod
    def _latest_records(
        events: Sequence[ManualReviewEvent | InvalidSnapshotReview],
    ) -> tuple[EvaluationRecord, ...]:
        latest: dict[tuple[str, str], ManualReviewEvent | InvalidSnapshotReview] = {}
        for item in events:
            pair = canonical_pair(item.workno_a, item.workno_b)
            current = latest.get(pair)
            if current is None or _event_key(item) > _event_key(current):
                latest[pair] = item
        return tuple(
            _record_from_review(latest[pair])
            for pair in sorted(latest)
        )

    build_latest_review_dataset = records

    def summary(self) -> EvaluationSummary:
        events = self._read_events()
        records = self._latest_records(events)
        latest_related = sum(r.outcome is CandidateReviewOutcome.RELATED for r in records)
        latest_not_related = sum(r.outcome is CandidateReviewOutcome.NOT_RELATED for r in records)
        latest_unsure = sum(r.outcome is CandidateReviewOutcome.UNSURE for r in records)
        decided = latest_related + latest_not_related
        valid_snapshots = sum(
            r.state is not EvaluationRecordState.INVALID_SNAPSHOT for r in records
        )
        invalid_snapshots = sum(r.state is EvaluationRecordState.INVALID_SNAPSHOT for r in records)
        legacy_snapshot_count = sum(
            r.state is not EvaluationRecordState.INVALID_SNAPSHOT
            and r.snapshot_schema_version == 1
            for r in records
        )
        policy_provenance_available_count = sum(
            r.state is not EvaluationRecordState.INVALID_SNAPSHOT
            and r.candidate_policy_id is not None
            and r.candidate_policy_version is not None
            for r in records
        )
        metric_state = _metric_state(decided, self._minimum_decided_labels)
        relation_counts = Counter(
            r.relation_type
            for r in records
            if r.outcome is CandidateReviewOutcome.RELATED and r.relation_type is not None
        )
        relation_type_counts = tuple(
            RelationTypeCount(relation_type=relation_type, count=relation_counts[relation_type])
            for relation_type in ManualRelationType
            if relation_counts[relation_type]
        )
        history_counts = Counter(canonical_pair(e.workno_a, e.workno_b) for e in events)
        values = list(history_counts.values())
        return EvaluationSummary(
            evaluation_policy_version=EVALUATION_POLICY_VERSION,
            minimum_decided_labels=self._minimum_decided_labels,
            total_review_events=len(events),
            reviewed_pair_count=len(records),
            latest_related_count=latest_related,
            latest_not_related_count=latest_not_related,
            latest_unsure_count=latest_unsure,
            decided_pair_count=decided,
            valid_snapshot_count=valid_snapshots,
            invalid_snapshot_count=invalid_snapshots,
            manual_related_rate=_rate(latest_related, decided),
            metric_state=metric_state,
            unsure_share=_rate(latest_unsure, len(records)),
            relation_type_counts=relation_type_counts,
            manual_bonus_related_pairs=sum(
                r.outcome is CandidateReviewOutcome.RELATED
                and r.relation_type
                in {ManualRelationType.BONUS_OF, ManualRelationType.LIMITED_BONUS_OF}
                for r in records
            ),
            review_events_mean_per_pair=statistics.mean(values) if values else 0.0,
            review_events_median_per_pair=statistics.median(values) if values else 0.0,
            review_events_max_per_pair=max(values, default=0),
            pairs_reviewed_once=sum(v == 1 for v in values),
            pairs_reviewed_more_than_once=sum(v > 1 for v in values),
            evidence_groups=self._evidence_groups(records),
            legacy_snapshot_count=legacy_snapshot_count,
            policy_provenance_available_count=policy_provenance_available_count,
            policy_distribution=self._policy_distribution(records),
        )

    @staticmethod
    def _policy_distribution(
        records: Sequence[EvaluationRecord],
    ) -> tuple[PolicyDistributionSummary, ...]:
        groups: dict[tuple[str, int | None], list[EvaluationRecord]] = defaultdict(list)
        for record in records:
            if record.state is EvaluationRecordState.INVALID_SNAPSHOT:
                continue
            key = (
                record.candidate_policy_id or "LEGACY_UNKNOWN_POLICY",
                record.candidate_policy_version,
            )
            groups[key].append(record)
        return tuple(
            PolicyDistributionSummary(
                policy_id=policy_id,
                policy_version=policy_version,
                record_count=len(items),
                decided_count=sum(
                    item.outcome
                    in {CandidateReviewOutcome.RELATED, CandidateReviewOutcome.NOT_RELATED}
                    for item in items
                ),
                related_count=sum(
                    item.outcome is CandidateReviewOutcome.RELATED for item in items
                ),
                not_related_count=sum(
                    item.outcome is CandidateReviewOutcome.NOT_RELATED for item in items
                ),
                unsure_count=sum(item.outcome is CandidateReviewOutcome.UNSURE for item in items),
            )
            for (policy_id, policy_version), items in sorted(groups.items())
        )

    def summary_by_policy(self) -> tuple[PolicyDistributionSummary, ...]:
        return self.summary().policy_distribution

    def summary_by_rj_threshold(self) -> tuple[EvidenceGroupSummary, ...]:
        return tuple(
            group
            for group in self.summary().evidence_groups
            if "+RJ<=" in group.group_key
        )

    def bonus_related_records(self) -> tuple[EvaluationRecord, ...]:
        return tuple(
            record
            for record in self.records()
            if record.outcome is CandidateReviewOutcome.RELATED
            and record.relation_type
            in {ManualRelationType.BONUS_OF, ManualRelationType.LIMITED_BONUS_OF}
        )

    def export_csv(self, path: str | Path) -> None:
        records = [self._export_record(record) for record in self.records()]
        fieldnames = [
            "workno_a", "workno_b", "outcome", "relation_type", "subject_workno",
            "target_workno", "reviewed_at", "review_event_id", "provenance", "state",
            "snapshot_schema_version", "candidate_policy_id", "candidate_policy_version",
            "application_version", "candidate_evaluated_at", "same_maker_id",
            "same_maker_name", "same_regist_date", "rj_numeric_distance",
            "local_maker_day_group_size", "local_known_maker_work_count",
        ]
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for record in records:
                payload = record.model_dump(mode="json")
                writer.writerow(payload)

    def _evidence_groups(
        self, records: Sequence[EvaluationRecord]
    ) -> tuple[EvidenceGroupSummary, ...]:
        groups: dict[str, list[EvaluationRecord]] = defaultdict(list)
        for record in records:
            if record.state is not EvaluationRecordState.VALID:
                continue
            if record.outcome not in {
                CandidateReviewOutcome.RELATED,
                CandidateReviewOutcome.NOT_RELATED,
            }:
                continue
            if record.same_maker_id is not None and record.same_regist_date is not None:
                groups["SAME_MAKER_ID + SAME_DATE"].append(record)
                for threshold in RJ_DISTANCE_THRESHOLDS:
                    if (
                        record.rj_numeric_distance is not None
                        and record.rj_numeric_distance <= threshold
                    ):
                        groups[f"SAME_MAKER_ID + SAME_DATE + RJ<={threshold}"].append(record)
            if record.same_maker_name is not None and record.same_regist_date is not None:
                groups["SAME_MAKER_NAME + SAME_DATE"].append(record)
                for threshold in RJ_DISTANCE_THRESHOLDS:
                    if (
                        record.rj_numeric_distance is not None
                        and record.rj_numeric_distance <= threshold
                    ):
                        groups[f"SAME_MAKER_NAME + SAME_DATE + RJ<={threshold}"].append(record)
        result = []
        for key in sorted(groups):
            items = groups[key]
            related = sum(r.outcome is CandidateReviewOutcome.RELATED for r in items)
            decided = len(items)
            result.append(
                EvidenceGroupSummary(
                    group_key=key,
                    related_count=related,
                    not_related_count=decided - related,
                    decided_count=decided,
                    manual_related_rate=_rate(related, decided),
                    metric_state=_metric_state(decided, self._minimum_decided_labels),
                )
            )
        return tuple(result)

    @staticmethod
    def _export_record(record: EvaluationRecord) -> EvaluationExportRecord:
        return EvaluationExportRecord(
            workno_a=record.workno_a,
            workno_b=record.workno_b,
            outcome=record.outcome,
            relation_type=record.relation_type,
            subject_workno=record.subject_workno,
            target_workno=record.target_workno,
            reviewed_at=record.reviewed_at,
            review_event_id=record.review_event_id,
            provenance=record.provenance,
            state=record.state,
            snapshot_schema_version=record.snapshot_schema_version,
            candidate_policy_id=record.candidate_policy_id,
            candidate_policy_version=record.candidate_policy_version,
            application_version=record.application_version,
            candidate_evaluated_at=record.candidate_evaluated_at,
            same_maker_id=record.same_maker_id,
            same_maker_name=record.same_maker_name,
            same_regist_date=record.same_regist_date,
            rj_numeric_distance=record.rj_numeric_distance,
            local_maker_day_group_size=record.local_maker_day_group_size,
            local_known_maker_work_count=record.local_known_maker_work_count,
        )


def _event_key(event: ManualReviewEvent | InvalidSnapshotReview) -> tuple:
    reviewed_at = event.reviewed_at
    if reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=UTC)
    return reviewed_at, event.id if event.id is not None else -1


def _record_from_review(event: ManualReviewEvent | InvalidSnapshotReview) -> EvaluationRecord:
    invalid = isinstance(event, InvalidSnapshotReview)
    policy_id: str | None = None
    policy_version: int | None = None
    application_version: str | None = None
    if invalid:
        snapshot = None
        state = EvaluationRecordState.INVALID_SNAPSHOT
        schema_version = event.snapshot_schema_version
        snapshot_error = event.snapshot_error
    else:
        snapshot = event.evidence_snapshot
        state = (
            EvaluationRecordState.UNSURE
            if event.outcome is CandidateReviewOutcome.UNSURE
            else EvaluationRecordState.VALID
        )
        schema_version = snapshot.schema_version
        snapshot_error = None
        policy_id = snapshot.policy_provenance.policy_id if snapshot.policy_provenance else None
        policy_version = (
            snapshot.policy_provenance.policy_version if snapshot.policy_provenance else None
        )
        application_version = (
            snapshot.policy_provenance.application_version
            if snapshot.policy_provenance
            else None
        )
    if invalid:
        policy_id = None
        policy_version = None
        application_version = None
    return EvaluationRecord(
        workno_a=event.workno_a,
        workno_b=event.workno_b,
        review_event_id=event.id if event.id is not None else 0,
        outcome=event.outcome,
        relation_type=event.relation_type,
        subject_workno=event.subject_workno,
        target_workno=event.target_workno,
        reviewed_at=event.reviewed_at,
        provenance=event.provenance,
        state=state,
        evidence_snapshot=snapshot,
        snapshot_schema_version=schema_version,
        snapshot_error=snapshot_error,
        candidate_policy_id=policy_id,
        candidate_policy_version=policy_version,
        application_version=application_version,
    )


def _metric_state(decided: int, minimum: int) -> EvaluationMetricState:
    return (
        EvaluationMetricState.SUFFICIENT_LABELS
        if decided >= minimum
        else EvaluationMetricState.INSUFFICIENT_LABELS
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
