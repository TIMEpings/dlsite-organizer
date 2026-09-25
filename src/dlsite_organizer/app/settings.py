"""Validated application settings and safe TOML persistence."""

from __future__ import annotations

import ctypes
import json
import os
import re
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from contextlib import suppress
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dlsite_organizer.app.locale import SUPPORTED_METADATA_LOCALES, default_metadata_locale
from dlsite_organizer.services.naming import DEFAULT_NAMING_TEMPLATE, NamingService

_SUPPORTED_METADATA_LOCALES = frozenset(SUPPORTED_METADATA_LOCALES)
_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DATE_DIRECTIVES = frozenset("aAbBcdHIjmMpSUwWxXyYZ%")


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


_FOLDERID_LOCAL_APP_DATA = _Guid(
    0xF1B32785,
    0x6FBA,
    0x4FCF,
    (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
)


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str = Field(default="maniax", min_length=1, pattern=r"^[a-z0-9_-]+$")
    base_url: str = "https://www.dlsite.com"
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    metadata_locale: str = Field(default_factory=lambda: default_metadata_locale())

    @field_validator("metadata_locale")
    @classmethod
    def validate_metadata_locale(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _SUPPORTED_METADATA_LOCALES:
            supported = ", ".join(sorted(_SUPPORTED_METADATA_LOCALES))
            raise ValueError(f"metadata_locale must be one of: {supported}")
        return normalized


class CacheSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    ttl_hours: float = Field(default=24.0, gt=0)
    allow_stale_on_error: bool = True


class StartupMode(StrEnum):
    """Which user-facing window is shown on the next application launch."""

    FULL = "full"
    LIGHTWEIGHT = "lightweight"


class AppSettings(BaseModel):
    """The single schema used by TOML, SettingsPage, and runtime services."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    naming_template: str = DEFAULT_NAMING_TEMPLATE
    cv_separator: str = ", "
    cv_prefix: str = ""
    cv_suffix: str = ""
    tag_separator: str = ", "
    max_tags: int = Field(default=0, ge=0, le=100)
    hide_general_age: bool = False
    date_format: str = "%Y-%m-%d"
    illegal_char_replacement: str = "_"
    startup_mode: StartupMode = StartupMode.LIGHTWEIGHT
    database_path: Path = Field(default_factory=lambda: default_data_dir() / "metadata.sqlite3")
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)

    @field_validator("naming_template")
    @classmethod
    def validate_naming_template(cls, value: str) -> str:
        # Keep settings-file validation and the live preview on exactly the
        # same renderer implementation.
        NamingService(value)
        return value

    @field_validator("illegal_char_replacement")
    @classmethod
    def validate_illegal_char_replacement(cls, value: str) -> str:
        if len(value) != 1 or _WINDOWS_ILLEGAL.search(value):
            raise ValueError("illegal_char_replacement must be one safe character")
        return value

    @field_validator("date_format")
    @classmethod
    def validate_date_format(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("date_format must not be empty")
        for match in re.finditer(r"%(.)", value):
            if match.group(1) not in _DATE_DIRECTIVES:
                raise ValueError(f"unsupported date format directive: %{match.group(1)}")
        return value


class SettingsError(ValueError):
    """A present configuration file could not be parsed or validated."""


def default_data_dir() -> Path:
    """Return the per-user directory used for mutable application data."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    elif os.name == "nt":
        base = _known_folder_local_app_data()
    else:
        base = Path.home() / ".local" / "share"
    return base / "dlsite-organizer"


def _known_folder_local_app_data() -> Path:
    """Resolve FOLDERID_LocalAppData using the Windows Known Folder API."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    get_known_folder_path = shell32.SHGetKnownFolderPath
    get_known_folder_path.argtypes = (
        ctypes.POINTER(_Guid),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_known_folder_path.restype = ctypes.c_long
    co_task_mem_free = ole32.CoTaskMemFree
    co_task_mem_free.argtypes = (ctypes.c_void_p,)
    co_task_mem_free.restype = None

    known_folder_path = ctypes.c_void_p()
    result = get_known_folder_path(
        ctypes.byref(_FOLDERID_LOCAL_APP_DATA),
        0,
        None,
        ctypes.byref(known_folder_path),
    )
    try:
        if result < 0:
            raise OSError(
                "SHGetKnownFolderPath(FOLDERID_LocalAppData) failed "
                f"with HRESULT 0x{result & 0xFFFFFFFF:08X}"
            )
        if not known_folder_path.value:
            raise OSError("SHGetKnownFolderPath(FOLDERID_LocalAppData) returned no path")
        path = ctypes.wstring_at(known_folder_path)
        if not path:
            raise OSError("SHGetKnownFolderPath(FOLDERID_LocalAppData) returned an empty path")
        return Path(path)
    finally:
        if known_folder_path.value:
            co_task_mem_free(known_folder_path)


def default_config_path() -> Path:
    """Return the user configuration file location."""
    return default_data_dir() / "config.toml"


def default_logs_path() -> Path:
    """Return the directory used by the rotating application log."""
    return default_data_dir() / "logs"


def load_settings(path: Path | None = None) -> AppSettings:
    """Load TOML settings, or return defaults when the file does not exist."""
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppSettings()
    try:
        with config_path.open("rb") as config_file:
            values = tomllib.load(config_file)
        settings = AppSettings.model_validate(values)
        if not settings.database_path.is_absolute():
            settings = settings.model_copy(
                update={"database_path": config_path.parent / settings.database_path}
            )
        return settings
    except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
        raise SettingsError(f"无法读取配置文件：{config_path}") from exc


class SettingsService:
    """Coordinate validation, atomic persistence, reset, and change listeners."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        config_path: Path | None = None,
    ) -> None:
        self._config_path = config_path or default_config_path()
        self._settings = settings if settings is not None else load_settings(self._config_path)
        self._listeners: list[Callable[[AppSettings], None]] = []

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def config_path(self) -> Path:
        return self._config_path

    def subscribe(self, listener: Callable[[AppSettings], None]) -> None:
        self._listeners.append(listener)

    def defaults(self) -> AppSettings:
        """Return fresh defaults without changing persisted/current values."""
        return AppSettings()

    def save(self, values: AppSettings | Mapping[str, object]) -> AppSettings:
        """Validate and atomically persist settings, then notify listeners."""
        settings = values if isinstance(values, AppSettings) else AppSettings.model_validate(values)
        _write_toml_atomic(self._config_path, settings)
        self._settings = settings
        for listener in tuple(self._listeners):
            listener(settings)
        return settings


def _write_toml_atomic(path: Path, settings: AppSettings) -> None:
    """Write a validated UTF-8 TOML document and replace the target atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _settings_to_toml(settings)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()


def _settings_to_toml(settings: AppSettings) -> str:
    lines = [
        f"naming_template = {_toml_string(settings.naming_template)}",
        f"cv_separator = {_toml_string(settings.cv_separator)}",
        f"cv_prefix = {_toml_string(settings.cv_prefix)}",
        f"cv_suffix = {_toml_string(settings.cv_suffix)}",
        f"tag_separator = {_toml_string(settings.tag_separator)}",
        f"max_tags = {settings.max_tags}",
        f"hide_general_age = {_toml_bool(settings.hide_general_age)}",
        f"date_format = {_toml_string(settings.date_format)}",
        f"illegal_char_replacement = {_toml_string(settings.illegal_char_replacement)}",
        f"startup_mode = {_toml_string(settings.startup_mode.value)}",
        f"database_path = {_toml_string(str(settings.database_path))}",
        "",
        "[provider]",
        f"section = {_toml_string(settings.provider.section)}",
        f"base_url = {_toml_string(settings.provider.base_url)}",
        f"timeout_seconds = {settings.provider.timeout_seconds:g}",
        f"metadata_locale = {_toml_string(settings.provider.metadata_locale)}",
        "",
        "[cache]",
        f"enabled = {_toml_bool(settings.cache.enabled)}",
        f"ttl_hours = {settings.cache.ttl_hours:g}",
        f"allow_stale_on_error = {_toml_bool(settings.cache.allow_stale_on_error)}",
        "",
    ]
    return "\n".join(lines)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
