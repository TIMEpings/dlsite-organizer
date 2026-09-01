"""SQLite persistence for append-only manual review events."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from dlsite_organizer.domain.evaluation import InvalidSnapshotReview
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    ManualReviewProvenance,
    canonical_pair,
    parse_candidate_evidence_snapshot,
)
from dlsite_organizer.domain.work_code import WorkCode
from dlsite_organizer.persistence.database import Base, Database

logger = logging.getLogger(__name__)


class ManualRelationReviewRecord(Base):
    __tablename__ = "manual_relation_reviews"
    __table_args__ = (
        Index("ix_manual_review_pair_reviewed", "workno_a", "workno_b", "reviewed_at"),
        Index("ix_manual_review_reviewed_at", "reviewed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workno_a: Mapped[str] = mapped_column(String(16), index=True)
    workno_b: Mapped[str] = mapped_column(String(16), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    relation_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_workno: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_workno: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_snapshot_json: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    provenance: Mapped[str] = mapped_column(String(64), default="MANUAL_USER_REVIEW")


class ManualReviewRepository:
    """Append and query manual review events; no UPDATE operation is exposed."""

    def __init__(self, database: Database) -> None:
        self._database = database
        if not database.initialized:
            raise RuntimeError("Database has not been initialized")
        Base.metadata.create_all(database._engine, tables=[ManualRelationReviewRecord.__table__])  # type: ignore[arg-type]

    def append(self, event: ManualReviewEvent) -> ManualReviewEvent:
        with self._database.session() as session:
            row = ManualRelationReviewRecord(
                workno_a=event.workno_a,
                workno_b=event.workno_b,
                outcome=event.outcome.value,
                relation_type=event.relation_type.value if event.relation_type else None,
                subject_workno=event.subject_workno,
                target_workno=event.target_workno,
                notes=event.notes,
                evidence_snapshot_json=event.evidence_snapshot.model_dump_json(),
                reviewed_at=_utc(event.reviewed_at),
                provenance=event.provenance.value,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            logger.info(
                "Manual review saved for %s/%s outcome=%s relation_type=%s",
                event.workno_a,
                event.workno_b,
                event.outcome.value,
                event.relation_type.value if event.relation_type else None,
            )
            return _to_domain(row)

    # Alias emphasizing event semantics.
    append_event = append

    def history_for_pair(self, workno_a: str, workno_b: str) -> tuple[ManualReviewEvent, ...]:
        a, b = canonical_pair(workno_a, workno_b)
        with self._database.session() as session:
            rows = session.scalars(
                select(ManualRelationReviewRecord)
                .where(
                    ManualRelationReviewRecord.workno_a == a,
                    ManualRelationReviewRecord.workno_b == b,
                )
                .order_by(
                    ManualRelationReviewRecord.reviewed_at.asc(),
                    ManualRelationReviewRecord.id.asc(),
                )
            ).all()
            return _valid_events(rows)

    def latest_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent | None:
        history = self.history_for_pair(workno_a, workno_b)
        return history[-1] if history else None

    latest_review_for_pair = latest_for_pair

    def list_reviews(self) -> tuple[ManualReviewEvent, ...]:
        with self._database.session() as session:
            rows = session.scalars(
                select(ManualRelationReviewRecord).order_by(
                    ManualRelationReviewRecord.reviewed_at.asc(),
                    ManualRelationReviewRecord.id.asc(),
                )
            ).all()
            return _valid_events(rows)

    list_manual_reviews = list_reviews

    def list_reviews_for_evaluation(
        self,
    ) -> tuple[ManualReviewEvent | InvalidSnapshotReview, ...]:
        """Read all rows for evaluation, retaining label-valid bad snapshots.

        The historical v0.8 methods intentionally skip malformed events.  This
        enhanced query is opt-in and only projects rows whose label/provenance
        fields are valid while their JSON snapshot cannot be decoded.
        """
        with self._database.session() as session:
            rows = session.scalars(
                select(ManualRelationReviewRecord).order_by(
                    ManualRelationReviewRecord.reviewed_at.asc(),
                    ManualRelationReviewRecord.id.asc(),
                )
            ).all()
            result: list[ManualReviewEvent | InvalidSnapshotReview] = []
            for row in rows:
                try:
                    result.append(_to_domain(row))
                    continue
                except Exception as error:
                    try:
                        outcome = CandidateReviewOutcome(row.outcome)
                        relation_type = (
                            ManualRelationType(row.relation_type)
                            if row.relation_type
                            else None
                        )
                        provenance = ManualReviewProvenance(row.provenance)
                    except Exception:
                        logger.warning(
                            "Skipping malformed manual review row %s", row.id, exc_info=True
                        )
                        continue
                    if not _label_fields_are_valid(
                        outcome,
                        relation_type,
                        row.subject_workno,
                        row.target_workno,
                        row.workno_a,
                        row.workno_b,
                    ):
                        logger.warning(
                            "Skipping malformed manual review row %s", row.id, exc_info=True
                        )
                        continue
                    result.append(
                        InvalidSnapshotReview(
                            id=row.id,
                            workno_a=row.workno_a,
                            workno_b=row.workno_b,
                            outcome=outcome,
                            relation_type=relation_type,
                            subject_workno=row.subject_workno,
                            target_workno=row.target_workno,
                            reviewed_at=_utc(row.reviewed_at),
                            provenance=provenance,
                            snapshot_error=str(error),
                            snapshot_schema_version=_snapshot_schema_version(
                                row.evidence_snapshot_json
                            ),
                        )
                    )
            return tuple(result)

    def reviews_for_work(self, workno: str) -> tuple[ManualReviewEvent, ...]:
        normalized = str(WorkCode.parse(workno))
        with self._database.session() as session:
            rows = session.scalars(
                select(ManualRelationReviewRecord)
                .where(
                    (ManualRelationReviewRecord.workno_a == normalized)
                    | (ManualRelationReviewRecord.workno_b == normalized)
                )
                .order_by(
                    ManualRelationReviewRecord.reviewed_at.asc(),
                    ManualRelationReviewRecord.id.asc(),
                )
            ).all()
            return _valid_events(rows)


def _to_domain(row: ManualRelationReviewRecord) -> ManualReviewEvent:
    return ManualReviewEvent(
        id=row.id,
        workno_a=row.workno_a,
        workno_b=row.workno_b,
        outcome=CandidateReviewOutcome(row.outcome),
        relation_type=ManualRelationType(row.relation_type) if row.relation_type else None,
        subject_workno=row.subject_workno,
        target_workno=row.target_workno,
        notes=row.notes,
        evidence_snapshot=parse_candidate_evidence_snapshot(row.evidence_snapshot_json),
        reviewed_at=_utc(row.reviewed_at),
        provenance=ManualReviewProvenance(row.provenance),
    )


def _valid_events(rows: Sequence[ManualRelationReviewRecord]) -> tuple[ManualReviewEvent, ...]:
    events: list[ManualReviewEvent] = []
    for row in rows:
        try:
            events.append(_to_domain(row))
        except Exception:
            logger.warning("Skipping malformed manual review row %s", row.id, exc_info=True)
    return tuple(events)


def _label_fields_are_valid(
    outcome: CandidateReviewOutcome,
    relation_type: ManualRelationType | None,
    subject_workno: str | None,
    target_workno: str | None,
    workno_a: str,
    workno_b: str,
) -> bool:
    """Validate non-snapshot semantics without inventing replacement evidence."""
    try:
        ManualReviewEvent(
            workno_a=workno_a,
            workno_b=workno_b,
            outcome=outcome,
            relation_type=relation_type,
            subject_workno=subject_workno,
            target_workno=target_workno,
            evidence_snapshot=CandidateEvidenceSnapshot(),
            reviewed_at=datetime.now(UTC),
        )
    except Exception:
        return False
    return True


def _snapshot_schema_version(payload: str) -> int | None:
    try:
        value = json.loads(payload).get("schema_version")
    except Exception:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC))
