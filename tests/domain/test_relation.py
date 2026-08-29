import pytest
from pydantic import ValidationError

from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    WorkRelation,
)


def test_relation_contract_carries_confidence_and_evidence() -> None:
    relation = WorkRelation(
        source_workno="rj01234567",
        target_workno="RJ01234568",
        relation_type=RelationType.TRANSLATION,
        confidence=Confidence.HIGH,
        evidence=[
            RelationEvidence(
                evidence_type=EvidenceType.SAME_MAKER,
                description="Both observations carry RG12345",
            )
        ],
        detection_source="future-analyzer",
    )

    assert relation.source_workno == "RJ01234567"
    assert relation.confidence is Confidence.HIGH
    assert len(relation.evidence) == 1


def test_relation_rejects_self_relation() -> None:
    with pytest.raises(ValidationError):
        WorkRelation(
            source_workno="RJ01234567",
            target_workno="RJ01234567",
            relation_type=RelationType.RELATED,
            detection_source="test",
        )
