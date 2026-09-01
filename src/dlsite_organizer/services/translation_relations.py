"""Map explicit DLsite translation metadata into domain relations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    TranslationRole,
    WorkRelation,
)
from dlsite_organizer.domain.work_code import WorkCode, WorkCodeError
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource


class TranslationAnalysisStatus(StrEnum):
    """How much trustworthy translation information was available."""

    NO_INFORMATION = "no_information"
    CONFIRMED = "confirmed"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


class TranslationContractError(ValueError):
    """A source payload that cannot represent a coherent translation topology."""


@dataclass(frozen=True, slots=True)
class TranslationAnalysis:
    """Application-ready translation facts for one queried work.

    ``relations`` contains only edges whose target work number was explicitly
    present in the source response.  It never contains a partially populated
    ``Work`` object or a relation discovered by fetching another work.
    """

    role: TranslationRole | None = None
    relations: tuple[WorkRelation, ...] = ()
    language: str | None = None
    status: TranslationAnalysisStatus = TranslationAnalysisStatus.NO_INFORMATION
    contract_issue: str | None = None


class TranslationRelationService:
    """Translate provider-local ``translation_info`` into confirmed edges."""

    _DETECTION_SOURCE = "dlsite.product_info_ajax.translation_info"

    def analyze(
        self,
        workno: str,
        translation_info: TranslationInfoSource | None,
    ) -> TranslationAnalysis:
        """Return safe translation facts without performing any network I/O."""
        if translation_info is None:
            return TranslationAnalysis()

        try:
            normalized_workno = str(WorkCode.parse(workno))
        except WorkCodeError as exc:
            return self._invalid(translation_info, str(exc))

        role_flags = {
            TranslationRole.ORIGINAL: translation_info.is_original is True,
            TranslationRole.TRANSLATION_PARENT: translation_info.is_parent is True,
            TranslationRole.TRANSLATION_CHILD: translation_info.is_child is True,
        }
        active_roles = [role for role, active in role_flags.items() if active]
        if len(active_roles) > 1:
            return self._invalid(
                translation_info,
                "translation_info marks more than one translation role",
            )
        if not active_roles:
            # Work numbers without an explicit role are not enough to choose a
            # topology.  In particular, do not infer a role from a reference.
            return TranslationAnalysis(language=translation_info.lang)

        role = active_roles[0]
        issues: list[str] = []
        relations: list[WorkRelation] = []

        if role is TranslationRole.ORIGINAL:
            if translation_info.original_workno is not None:
                issues.append("original role unexpectedly contains original_workno")
            if translation_info.parent_workno is not None:
                issues.append("original role unexpectedly contains parent_workno")
            if translation_info.child_worknos:
                issues.append("original role unexpectedly contains child_worknos")
        elif role is TranslationRole.TRANSLATION_PARENT:
            if translation_info.parent_workno is not None:
                issues.append("translation parent unexpectedly contains parent_workno")
            if translation_info.original_workno is None:
                issues.append("translation parent is missing original_workno")
            else:
                self._append_relation(
                    relations,
                    issues,
                    normalized_workno,
                    translation_info.original_workno,
                    RelationType.TRANSLATION_OF,
                    "original_workno",
                )
            for child_workno in translation_info.child_worknos:
                self._append_relation(
                    relations,
                    issues,
                    normalized_workno,
                    child_workno,
                    RelationType.HAS_TRANSLATION_CHILD,
                    "child_worknos",
                )
        else:
            if translation_info.child_worknos:
                issues.append("translation child unexpectedly contains child_worknos")
            if translation_info.parent_workno is None:
                issues.append("translation child is missing parent_workno")
            else:
                self._append_relation(
                    relations,
                    issues,
                    normalized_workno,
                    translation_info.parent_workno,
                    RelationType.CHILD_OF_TRANSLATION,
                    "parent_workno",
                )
            if translation_info.original_workno is None:
                issues.append("translation child is missing original_workno")
            else:
                self._append_relation(
                    relations,
                    issues,
                    normalized_workno,
                    translation_info.original_workno,
                    RelationType.TRANSLATION_OF,
                    "original_workno",
                )

        status = (
            TranslationAnalysisStatus.INCOMPLETE
            if issues
            else TranslationAnalysisStatus.CONFIRMED
        )
        return TranslationAnalysis(
            role=role,
            relations=tuple(relations),
            language=translation_info.lang,
            status=status,
            contract_issue="; ".join(issues) if issues else None,
        )

    def _append_relation(
        self,
        relations: list[WorkRelation],
        issues: list[str],
        source_workno: str,
        raw_target_workno: str,
        relation_type: RelationType,
        field_name: str,
    ) -> None:
        try:
            target_workno = str(WorkCode.parse(raw_target_workno))
        except (TypeError, WorkCodeError) as exc:
            issues.append(f"{field_name} contains an invalid workno: {exc}")
            return
        if source_workno == target_workno:
            issues.append(f"{field_name} refers to the queried work itself")
            return
        relations.append(
            WorkRelation(
                source_workno=source_workno,
                target_workno=target_workno,
                relation_type=relation_type,
                confidence=Confidence.CONFIRMED,
                evidence=[
                    RelationEvidence(
                        evidence_type=EvidenceType.EXPLICIT_TRANSLATION_REFERENCE,
                        description="DLsite translation_info explicitly references a work number.",
                        attributes={
                            "field": f"translation_info.{field_name}",
                            "value": target_workno,
                        },
                    )
                ],
                detection_source=self._DETECTION_SOURCE,
            )
        )

    @staticmethod
    def _invalid(
        translation_info: TranslationInfoSource,
        message: str,
    ) -> TranslationAnalysis:
        # The error type is deliberately used as the domain vocabulary for the
        # issue, but exposed to callers as data so a malformed topology cannot
        # crash a successful metadata lookup.
        issue = TranslationContractError(message)
        return TranslationAnalysis(
            language=translation_info.lang,
            status=TranslationAnalysisStatus.INVALID,
            contract_issue=str(issue),
        )
