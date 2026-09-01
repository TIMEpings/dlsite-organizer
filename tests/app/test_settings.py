from pathlib import Path

import pytest
from PySide6.QtCore import QLocale

from dlsite_organizer.app.locale import metadata_locale_for
from dlsite_organizer.app.settings import (
    AppSettings,
    CacheSettings,
    ProviderSettings,
    SettingsError,
    SettingsService,
    StartupMode,
    load_settings,
)
from dlsite_organizer.services.naming import DEFAULT_NAMING_TEMPLATE


def test_missing_config_uses_fresh_profile_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "dlsite_organizer.app.settings.default_metadata_locale",
        lambda: "en_us",
    )
    settings = load_settings(tmp_path / "missing.toml")
    assert settings.naming_template == DEFAULT_NAMING_TEMPLATE
    assert settings.startup_mode is StartupMode.LIGHTWEIGHT
    assert settings.provider.metadata_locale == "en_us"
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


def test_settings_service_saves_atomically_and_round_trips_new_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    database_path = tmp_path / "metadata.sqlite3"
    service = SettingsService(
        AppSettings(
            database_path=database_path,
            naming_template="[{maker_name}][{rjcode}] {title}",
            cv_separator=" / ",
            max_tags=3,
            hide_general_age=True,
            date_format="%Y%m%d",
            provider=ProviderSettings(metadata_locale="zh_cn"),
            cache=CacheSettings(ttl_hours=12),
        ),
        config_path=config_path,
    )

    saved = service.save(service.settings)
    loaded = load_settings(config_path)

    assert saved == loaded
    assert loaded.cv_separator == " / "
    assert loaded.max_tags == 3
    assert loaded.hide_general_age is True
    assert loaded.provider.metadata_locale == "zh_cn"
    assert loaded.cache.ttl_hours == 12
    assert not any(config_path.parent.glob(f".{config_path.name}.*.tmp"))


def test_legacy_toml_uses_defaults_for_new_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('naming_template = "[{workno}] {title}"\n', encoding="utf-8")
    monkeypatch.setattr(
        "dlsite_organizer.app.settings.default_metadata_locale",
        lambda: "zh_tw",
    )

    settings = load_settings(path)

    assert settings.naming_template == "[{workno}] {title}"
    assert settings.provider.metadata_locale == "zh_tw"
    assert settings.max_tags == 0


def test_startup_mode_round_trips_and_legacy_config_defaults_to_lightweight(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('naming_template = "[{workno}] {title}"\n', encoding="utf-8")
    assert load_settings(legacy).startup_mode is StartupMode.LIGHTWEIGHT

    path = tmp_path / "config.toml"
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=path,
    )
    updated = service.settings.model_copy(update={"startup_mode": StartupMode.LIGHTWEIGHT})
    saved = service.save(updated)

    assert saved.startup_mode is StartupMode.LIGHTWEIGHT
    assert load_settings(path).startup_mode is StartupMode.LIGHTWEIGHT


@pytest.mark.parametrize(
    ("system_locale", "expected"),
    [
        ("ja_JP", "ja_jp"),
        ("en_US", "en_us"),
        ("zh_CN", "zh_cn"),
        ("zh_SG", "zh_cn"),
        ("zh_TW", "zh_tw"),
        ("zh_HK", "zh_tw"),
        ("zh_MO", "zh_tw"),
        ("ko_KR", "ko_kr"),
        ("fr_FR", "ja_jp"),
    ],
)
def test_system_locale_maps_to_verified_provider_locale(
    system_locale: str,
    expected: str,
) -> None:
    assert metadata_locale_for(QLocale(system_locale)) == expected


def test_existing_explicit_settings_are_preserved_when_system_locale_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'startup_mode = "full"\n'
        'naming_template = "[{rjcode}] custom"\n'
        '[provider]\n'
        'metadata_locale = "ja_jp"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "dlsite_organizer.app.settings.default_metadata_locale",
        lambda: "zh_cn",
    )

    settings = load_settings(path)

    assert settings.startup_mode is StartupMode.FULL
    assert settings.naming_template == "[{rjcode}] custom"
    assert settings.provider.metadata_locale == "ja_jp"
