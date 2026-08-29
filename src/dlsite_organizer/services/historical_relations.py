"""Derive aggregated historical confirmed relations from metadata observations."""
# ruff: noqa
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from dlsite_organizer.domain.relation import Confidence, RelationType, WorkRelation
from dlsite_organizer.persistence.metadata_store import MetadataObservation, MetadataStore
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.domain.work_code import normalize_rjcode
from dlsite_organizer.services.translation_relations import TranslationRelationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RelationEvidenceRecord:
    observation_id: int
    observed_at: datetime
    provider_source: str
    field: str
    referenced_workno: str


@dataclass(frozen=True, slots=True)
class HistoricalRelation:
    subject_workno: str
    relation_type: RelationType
    target_workno: str
    confidence: Confidence
    first_seen: datetime
    last_seen: datetime
    observation_count: int
    evidence: tuple[RelationEvidenceRecord, ...]

    @property
    def source_workno(self) -> str:
        return self.subject_workno


@dataclass(frozen=True, slots=True)
class HistoricalRelations:
    outgoing: tuple[HistoricalRelation, ...] = ()
    incoming: tuple[HistoricalRelation, ...] = ()


class _ObservationStore(Protocol):
    def list_all_observations(self) -> tuple[MetadataObservation, ...]: ...


class HistoricalRelationService:
    """Interpret persisted explicit translation payloads; never performs I/O."""

    def __init__(self, metadata_store: _ObservationStore, translation_service: TranslationRelationService | None = None) -> None:
        self._store = metadata_store
        self._translation = translation_service or TranslationRelationService()

    def for_work(self, workno: str) -> HistoricalRelations:
        workno = normalize_rjcode(workno)
        outgoing: list[HistoricalRelation] = []
        incoming: list[HistoricalRelation] = []
        aggregates: dict[tuple[str, RelationType, str], list[RelationEvidenceRecord]] = {}
        for observation in self._store.list_all_observations():
            for relation in self._relations_for_observation(observation):
                key = (relation.source_workno, relation.relation_type, relation.target_workno)
                field = next((e.attributes.get("field", "translation_info") for e in relation.evidence), "translation_info")
                aggregates.setdefault(key, []).append(
                    RelationEvidenceRecord(observation.id, observation.observed_at, observation.source, str(field), relation.target_workno)
                )
        for (subject, rel_type, target), evidence in aggregates.items():
            evidence.sort(key=lambda e: (e.observed_at, e.observation_id))
            item = HistoricalRelation(subject, rel_type, target, Confidence.CONFIRMED, evidence[0].observed_at, evidence[-1].observed_at, len(evidence), tuple(evidence))
            if subject == workno:
                outgoing.append(item)
            if target == workno:
                incoming.append(item)
        key = lambda r: (r.relation_type.value, r.target_workno if r.subject_workno == workno else r.subject_workno)
        return HistoricalRelations(tuple(sorted(outgoing, key=key)), tuple(sorted(incoming, key=key)))

    def _relations_for_observation(self, observation: MetadataObservation) -> tuple[WorkRelation, ...]:
        if not observation.translation_json:
            return ()
        try:
            info = TranslationInfoSource.model_validate(json.loads(observation.translation_json))
            return self._translation.analyze(observation.workno, info).relations
        except Exception:
            logger.warning("Skipping malformed historical translation observation %s", observation.id, exc_info=True)
            return ()
