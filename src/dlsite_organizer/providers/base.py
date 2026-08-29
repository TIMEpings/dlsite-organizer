"""Provider contracts consumed by application services."""

from typing import Protocol

from dlsite_organizer.domain.work import Work


class WorkProvider(Protocol):
    """Retrieve normalized metadata without exposing transport details."""

    def fetch_work(self, workno: str) -> Work:
        """Fetch one work and convert it to the domain model."""
        ...
