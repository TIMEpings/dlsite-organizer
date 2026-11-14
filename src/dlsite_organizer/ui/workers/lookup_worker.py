"""Background execution adapter for the lookup use case."""

from __future__ import annotations
# ruff: noqa

import logging
from dataclasses import replace
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.bonus_search import (
    BonusSearchRequest, BonusSearchResult, BonusSearchService,
)
from dlsite_organizer.services.lookup import LookupFailure, LookupFreshness, LookupService

logger = logging.getLogger(__name__)


class LookupWorker(QObject):
    """Run services off the main thread and emit UI-neutral values."""

    result_ready = Signal(object)
    cover_ready = Signal(str, bytes)
    translations_ready = Signal(object)
    translations_finished = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        lookup_service: LookupService,
        cover_service: CoverService,
        raw_workno: str,
        force_refresh: bool = False,
    ) -> None:
        super().__init__()
        self._lookup_service = lookup_service.snapshot()
        self._cover_service = cover_service
        self._raw_workno = raw_workno
        self._force_refresh = force_refresh
        self._translations_cancelled = Event()

    def cancel_translations(self) -> None:
        self._translations_cancelled.set()

    @Slot()
    def run(self) -> None:
        """Execute lookup and optional cover retrieval in the worker thread."""
        try:
            result = self._lookup_service.lookup(
                self._raw_workno, force_refresh=self._force_refresh, prefer_bonus_review=True,
            )
            try:
                result = replace(result, bonus_preview=self._lookup_service.preview_bonuses(result))
            except Exception:
                logger.exception("Local bonus preview failed for %s", result.work.workno)
            self.result_ready.emit(result)
            work = result.work
            cover = self._cover_service.cached_cover_for(work.workno)
            if work.cover_url and (
                self._force_refresh
                or self._cover_service.cached_cover_for(work.workno, url=work.cover_url) is None
            ):
                cover = self._cover_service.fetch_for_work(work.workno, work.cover_url) or cover
            self.cover_ready.emit(work.workno, cover or b"")
            try:
                self._lookup_service.discover_translations(
                    result, publish=self.translations_ready.emit,
                    cancelled=self._translations_cancelled.is_set,
                    allow_online=result.freshness not in {
                        LookupFreshness.CACHE_STALE_FALLBACK, LookupFreshness.LOCAL_BONUS_REVIEW,
                    },
                )
            except Exception:
                logger.exception("Directed translation lookup failed for %s", result.work.workno)
            finally:
                self.translations_finished.emit()
        except LookupFailure as exc:
            self.failed.emit(exc.user_message)
        except Exception:
            logger.exception("Unexpected exception escaped lookup services")
            self.failed.emit("查询时发生意外错误，详细信息已写入日志。")
        finally:
            self.finished.emit()


class BonusSearchWorker(QObject):
    """Run the bonus search with the same thread lifetime as an ordinary lookup."""

    finished = Signal()
    result_ready = Signal(object)
    progress_changed = Signal(str)

    def __init__(self, service: BonusSearchService, request: BonusSearchRequest) -> None:
        super().__init__()
        self._service = service
        self._request = request
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        result: BonusSearchResult | None = None
        try:
            result = self._service.search(
                self._request,
                cancelled=self._cancelled.is_set,
                progress=self.progress_changed.emit,
            )
        except Exception:
            logger.exception("Unexpected exception during limited bonus search")
        finally:
            self.result_ready.emit(result)
            self.finished.emit()
