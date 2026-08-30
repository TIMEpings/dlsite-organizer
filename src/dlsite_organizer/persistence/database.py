"""SQLAlchemy schema and lifecycle for local metadata observations."""

from __future__ import annotations
# ruff: noqa

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine, text
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


class RenameTransactionRecord(Base):
    """Durable header for one confirmed rename batch."""

    __tablename__ = "rename_transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    recovery_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_error: Mapped[str | None] = mapped_column(String, nullable=True)
    recovery_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RenameOperationRecord(Base):
    """Durable intent and outcome for one operation in a rename transaction."""

    __tablename__ = "rename_operations"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", "sequence", name="uq_rename_operation_transaction_sequence"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("rename_transactions.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    source_path: Mapped[str] = mapped_column(String)
    target_path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undo_status: Mapped[str] = mapped_column(String(32), index=True)
    undo_error: Mapped[str | None] = mapped_column(String, nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Database:
    """Own the SQLite engine and initialize the small application schema."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        self._initialized = False

    def initialize(self) -> None:
        """Create the data directory and missing schema without writing observations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        core_tables = [
            t
            for t in Base.metadata.tables.values()
            if t.name
            not in {
                "work_metadata_cache",
                "metadata_observations",
                "manual_relation_reviews",
            }
        ]
        Base.metadata.create_all(self._engine, tables=core_tables)
        # Lightweight forward migration for development databases created by v0.4.
        with self._engine.begin() as connection:
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(rename_transactions)"))
            }
            for name, definition in (
                ("recovery_stage", "VARCHAR(64)"),
                ("recovery_error", "VARCHAR"),
                ("recovery_sequence", "INTEGER"),
            ):
                if name not in columns:
                    connection.execute(
                        text(f"ALTER TABLE rename_transactions ADD COLUMN {name} {definition}")
                    )
        self._initialized = True

    def initialize_metadata(self) -> None:
        """Create metadata tables after the core/journal schema is initialized."""
        if not self._initialized:
            raise RuntimeError("Database has not been initialized")
        Base.metadata.create_all(
            self._engine,
            tables=[
                t
                for t in Base.metadata.tables.values()
                if t.name in {"work_metadata_cache", "metadata_observations"}
            ],
        )
        # Additive migration for metadata history created before transient
        # bonus evidence was retained.  Existing rows remain NULL/unknown.
        with self._engine.begin() as connection:
            columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(metadata_observations)"))
            }
            if "bonus_evidence_json" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE metadata_observations "
                        "ADD COLUMN bonus_evidence_json TEXT"
                    )
                )
        # Manual review events are initialized with the metadata subsystem so
        # the v0.7 minimum-core schema remains backwards compatible.
        from dlsite_organizer.persistence.manual_reviews import ManualRelationReviewRecord

        Base.metadata.create_all(self._engine, tables=[ManualRelationReviewRecord.__table__])  # pyright: ignore[reportArgumentType]

    @property
    def initialized(self) -> bool:
        """Whether all required tables were successfully initialized."""
        return self._initialized

    def session(self):
        """Create a short-lived session for one durable journal operation."""
        if not self._initialized:
            raise RuntimeError("Database has not been initialized")
        from sqlalchemy.orm import Session

        return Session(self._engine)

    def dispose(self) -> None:
        """Release pooled SQLite connections."""
        self._engine.dispose()
