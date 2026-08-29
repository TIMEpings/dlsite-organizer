"""Composition root for infrastructure, services, and UI dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dlsite_organizer.app.settings import AppSettings
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.persistence.rename_journal import (
    RenameJournal,
    TransactionJournal,
    UnavailableRenameJournal,
)
from dlsite_organizer.providers.dlsite.client import DlsiteProvider
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_planner import RenamePlanner
from dlsite_organizer.services.undo_service import UndoService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationComponents:
    """Long-lived objects composed once at startup."""

    lookup_service: LookupService
    organizer_service: OrganizerService
    cover_service: CoverService
    database: Database
    rename_executor: RenameExecutor
    undo_service: UndoService
    rename_journal: RenameJournal


def build_components(settings: AppSettings) -> ApplicationComponents:
    """Compose concrete infrastructure behind service-facing boundaries."""
    provider = DlsiteProvider(
        section=settings.provider.section,
        base_url=settings.provider.base_url,
        timeout_seconds=settings.provider.timeout_seconds,
    )
    naming = NamingService(settings.naming_template)
    database = Database(settings.database_path)
    try:
        database.initialize()
    except Exception:
        logger.exception('Database initialization failed; metadata persistence disabled')
        metadata_store = None
    else:
        metadata_store = MetadataStore(database) if settings.cache.enabled else None
    lookup_service = LookupService(provider, naming, metadata_store=metadata_store,
        cache_ttl_hours=settings.cache.ttl_hours, allow_stale_on_error=settings.cache.allow_stale_on_error)
    organizer_service = OrganizerService(
        lookup_service,
        scanner=FolderScanner(),
        planner=RenamePlanner(naming),
    )
    if not database.initialized:
        journal: RenameJournal = UnavailableRenameJournal()
    else:
        journal = TransactionJournal(database)
    rename_executor = RenameExecutor(journal)
    undo_service = UndoService(journal)
    return ApplicationComponents(
        lookup_service=lookup_service,
        organizer_service=organizer_service,
        cover_service=CoverService(),
        database=database,
        rename_executor=rename_executor,
        undo_service=undo_service,
        rename_journal=journal,
    )
