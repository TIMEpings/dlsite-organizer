"""The manual work lookup use case."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from dlsite_organizer.domain.relation import TranslationRole, WorkRelation
from dlsite_organizer.domain.work import Work
from dlsite_organizer.domain.work_code import WorkCodeError, normalize_rjcode
from dlsite_organizer.providers.base import WorkProvider
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    DlsiteParseError,
    WorkNotFoundError,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationRelationService,
)

logger = logging.getLogger(__name__)


class LookupFailureKind(StrEnum):
    INVALID_CODE = "invalid_code"
    NOT_FOUND = "not_found"
    CONNECTION = "connection"
    RESPONSE = "response"
    UNEXPECTED = "unexpected"


class LookupFailure(Exception):
    """A user-safe lookup error returned across the UI boundary."""

    def __init__(self, kind: LookupFailureKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.user_message = message


class _SourceAwareLookup(Protocol):
    @property
    def work(self) -> Work:
        ...

    @property
    def translation_info(self) -> TranslationInfoSource | None:
        ...


@runtime_checkable
class _SourceAwareProvider(Protocol):
    def fetch_work_lookup(self, workno: str) -> _SourceAwareLookup:
        ...


@dataclass(frozen=True, slots=True)
class LookupResult:
    work: Work
    formatted_name: str
    translation: TranslationAnalysis = field(default_factory=TranslationAnalysis)

    @property
    def translation_role(self) -> TranslationRole | None:
        """Compatibility-friendly shortcut for the interpreted role."""
        return self.translation.role

    @property
    def relations(self) -> tuple[WorkRelation, ...]:
        """Compatibility-friendly shortcut for interpreted relation edges."""
        return self.translation.relations


class LookupService:
    """Validate a code, fetch a Work, and produce its formatted name."""

    def __init__(
        self,
        provider: WorkProvider | _SourceAwareProvider,
        naming: NamingService,
        translation_relations: TranslationRelationService | None = None,
    ) -> None:
        self._provider = provider
        self._naming = naming
        self._translation_relations = translation_relations or TranslationRelationService()

    def lookup(self, raw_workno: str) -> LookupResult:
        """Execute the complete metadata lookup use case."""
        try:
            workno = normalize_rjcode(raw_workno)
        except WorkCodeError as exc:
            raise LookupFailure(LookupFailureKind.INVALID_CODE, str(exc)) from exc

        logger.info("Looking up work %s", workno)
        try:
            work, translation_info = self._fetch_work(workno)
        except WorkNotFoundError as exc:
            raise LookupFailure(
                LookupFailureKind.NOT_FOUND,
                f"未在当前 DLsite 类别中找到作品 {workno}。",
            ) from exc
        except DlsiteConnectionError as exc:
            raise LookupFailure(
                LookupFailureKind.CONNECTION,
                "连接 DLsite 失败，请检查网络后稍后重试。",
            ) from exc
        except (DlsiteHttpError, DlsiteParseError) as exc:
            raise LookupFailure(
                LookupFailureKind.RESPONSE,
                "DLsite 返回的数据暂时无法读取，请稍后重试。",
            ) from exc
        except Exception as exc:
            logger.exception("Unexpected lookup failure for %s", workno)
            raise LookupFailure(
                LookupFailureKind.UNEXPECTED,
                "查询时发生意外错误，详细信息已写入日志。",
            ) from exc
        return LookupResult(
            work=work,
            formatted_name=self._naming.format(work),
            translation=self._translation_relations.analyze(workno, translation_info),
        )

    def _fetch_work(self, workno: str) -> tuple[Work, TranslationInfoSource | None]:
        """Prefer the source-aware provider extension without breaking simple providers."""
        if isinstance(self._provider, _SourceAwareProvider):
            lookup = self._provider.fetch_work_lookup(workno)
            return lookup.work, lookup.translation_info
        return self._provider.fetch_work(workno), None
