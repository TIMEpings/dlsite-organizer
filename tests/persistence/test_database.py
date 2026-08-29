from pathlib import Path

from sqlalchemy import create_engine, inspect

from dlsite_organizer.persistence.database import Database


def test_database_initializes_minimum_schema(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "metadata.sqlite3"
    database = Database(path)

    database.initialize()
    database.dispose()

    assert path.exists()
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    assert set(inspect(engine).get_table_names()) == {
        "rename_operations",
        "rename_transactions",
        "work_observations",
    }
    engine.dispose()
