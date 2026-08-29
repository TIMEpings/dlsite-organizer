from pathlib import Path

import pytest

from dlsite_organizer.app.settings import SettingsError, load_settings


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.toml")
    assert settings.naming_template == "[{maker_name}][{workno}] {title}"
    assert settings.provider.section == "maniax"


def test_loads_toml_overrides(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'naming_template = "[{workno}] {title}"\n'
        'database_path = "custom.sqlite3"\n'
        "[provider]\n"
        'section = "home"\n'
        "timeout_seconds = 20\n",
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.provider.section == "home"
    assert settings.provider.timeout_seconds == 20
    assert settings.database_path == tmp_path / "custom.sqlite3"


def test_invalid_config_is_not_silently_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not valid toml =", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(path)
