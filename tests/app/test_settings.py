import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QLocale

from dlsite_organizer.app import settings as settings_module
from dlsite_organizer.app.locale import metadata_locale_for
from dlsite_organizer.app.settings import (
    AppSettings,
    CacheSettings,
    ProviderSettings,
    SettingsError,
    SettingsService,
    StartupMode,
    default_data_dir,
    load_settings,
)
from dlsite_organizer.services.naming import DEFAULT_NAMING_TEMPLATE


def test_default_data_dir_uses_nonempty_local_app_data_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_app_data = tmp_path / "configured-local-app-data"
    monkeypatch.setattr(
        settings_module,
        "os",
        SimpleNamespace(name="nt", environ={"LOCALAPPDATA": str(local_app_data)}),
    )
    monkeypatch.setattr(
        settings_module,
        "_known_folder_local_app_data",
        lambda: pytest.fail("Known Folder fallback should not be used"),
    )

    assert default_data_dir() == local_app_data / "dlsite-organizer"


@pytest.mark.parametrize("local_app_data", [None, ""])
def test_default_data_dir_uses_windows_known_folder_when_local_app_data_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_app_data: str | None,
) -> None:
    known_folder = tmp_path / "Windows" / "LocalAppData"
    environment = {} if local_app_data is None else {"LOCALAPPDATA": local_app_data}
    monkeypatch.setattr(
        settings_module,
        "os",
        SimpleNamespace(name="nt", environ=environment),
    )
    monkeypatch.setattr(settings_module, "_known_folder_local_app_data", lambda: known_folder)

    expected = known_folder / "dlsite-organizer"
    assert default_data_dir() == expected
    assert settings_module.default_config_path() == expected / "config.toml"
    assert settings_module.default_logs_path() == expected / "logs"


def test_default_data_dir_keeps_non_windows_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_module,
        "os",
        SimpleNamespace(name="posix", environ={}),
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert default_data_dir() == tmp_path / ".local" / "share" / "dlsite-organizer"


def test_windows_known_folder_failure_does_not_choose_a_different_profile_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_module,
        "os",
        SimpleNamespace(name="nt", environ={}),
    )

    def fail_resolution() -> Path:
        raise OSError("Known Folder resolution failed")

    monkeypatch.setattr(settings_module, "_known_folder_local_app_data", fail_resolution)
    with pytest.raises(OSError, match="Known Folder resolution failed"):
        default_data_dir()


@pytest.mark.parametrize("hresult", [0, -2147024891])
def test_known_folder_resolver_uses_local_app_data_guid_and_frees_api_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hresult: int,
) -> None:
    expected = tmp_path / "Known Folder LocalAppData"
    allocated_path = ctypes.create_unicode_buffer(str(expected))
    allocated_pointer = ctypes.cast(allocated_path, ctypes.c_void_p).value
    freed_pointers: list[int | None] = []

    class FunctionStub:
        def __init__(self, implementation):
            self.implementation = implementation

        def __call__(self, *arguments):
            return self.implementation(*arguments)

    def get_known_folder_path(folder_id, flags, token, output) -> int:
        guid = ctypes.cast(folder_id, ctypes.POINTER(settings_module._Guid)).contents
        assert (guid.data1, guid.data2, guid.data3, tuple(guid.data4)) == (
            0xF1B32785,
            0x6FBA,
            0x4FCF,
            (0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
        )
        assert flags == 0
        assert token is None
        output_pointer = ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))
        output_pointer.contents.value = allocated_pointer
        return hresult

    def free_task_memory(pointer: ctypes.c_void_p) -> None:
        freed_pointers.append(pointer.value)

    libraries = {
        "shell32": SimpleNamespace(SHGetKnownFolderPath=FunctionStub(get_known_folder_path)),
        "ole32": SimpleNamespace(CoTaskMemFree=FunctionStub(free_task_memory)),
    }
    monkeypatch.setattr(
        settings_module.ctypes,
        "WinDLL",
        lambda name, **_kwargs: libraries[name],
        raising=False,
    )

    if hresult < 0:
        with pytest.raises(OSError, match="HRESULT 0x80070005"):
            settings_module._known_folder_local_app_data()
    else:
        assert settings_module._known_folder_local_app_data() == expected
    assert freed_pointers == [allocated_pointer]


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
