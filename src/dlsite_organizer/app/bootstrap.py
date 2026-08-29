"""Composition root for infrastructure, services, and UI dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from dlsite_organizer.app.settings import AppSettings
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.providers.dlsite.client import DlsiteProvider
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_planner import RenamePlanner


@dataclass(frozen=True, slots=True)
class ApplicationComponents:
    """Long-lived objects composed once at startup."""

    lookup_service: LookupService
    organizer_service: OrganizerService
    cover_service: CoverService
    database: Database


def build_components(settings: AppSettings) -> ApplicationComponents:
    """Compose concrete infrastructure behind service-facing boundaries."""
    provider = DlsiteProvider(
        section=settings.provider.section,
        base_url=settings.provider.base_url,
        timeout_seconds=settings.provider.timeout_seconds,
    )
    naming = NamingService(settings.naming_template)
    lookup_service = LookupService(provider, naming)
    organizer_service = OrganizerService(
        lookup_service,
        scanner=FolderScanner(),
        planner=RenamePlanner(naming),
    )
    database = Database(settings.database_path)
    database.initialize()
    return ApplicationComponents(
        lookup_service=lookup_service,
        organizer_service=organizer_service,
        cover_service=CoverService(),
        database=database,
    )
