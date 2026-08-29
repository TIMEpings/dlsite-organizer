"""SQLAlchemy schema and lifecycle for local metadata observations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WorkObservation(Base):
    """Minimal historical core metadata row for future repository work."""

    __tablename__ = "work_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workno: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String)
    maker_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    maker_name: Mapped[str | None] = mapped_column(String, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class Database:
    """Own the SQLite engine and initialize only the minimum schema."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._engine = create_engine(f"sqlite:///{path.as_posix()}")

    def initialize(self) -> None:
        """Create the data directory and missing schema without writing observations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self._engine)

    def dispose(self) -> None:
        """Release pooled SQLite connections."""
        self._engine.dispose()
