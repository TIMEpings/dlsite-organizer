from pathlib import Path

from sqlalchemy import inspect

from dlsite_organizer.app.bootstrap import build_components
from dlsite_organizer.app.settings import AppSettings


def test_fresh_profile_build_initializes_schema_without_provider_access(tmp_path: Path) -> None:
    database_path = tmp_path / "profile" / "metadata.sqlite3"
    components = build_components(AppSettings(database_path=database_path))

    try:
        assert database_path.is_file()
        tables = set(inspect(components.database._engine).get_table_names())
        assert {
            "work_observations",
            "rename_transactions",
            "rename_operations",
            "work_metadata_cache",
            "metadata_observations",
            "manual_relation_reviews",
        } <= tables
        assert components.rename_executor.available
    finally:
        components.database.dispose()
