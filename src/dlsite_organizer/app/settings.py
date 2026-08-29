"""TOML-backed application settings with safe built-in defaults."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dlsite_organizer.services.naming import DEFAULT_NAMING_TEMPLATE


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str = Field(default="maniax", min_length=1, pattern=r"^[a-z0-9_-]+$")
    base_url: str = "https://www.dlsite.com"
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)


class CacheSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    ttl_hours: float = Field(default=24.0, gt=0)
    allow_stale_on_error: bool = True


class AppSettings(BaseModel):
    """Validated values used to compose the application."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    naming_template: str = DEFAULT_NAMING_TEMPLATE
    database_path: Path = Field(default_factory=lambda: default_data_dir() / "metadata.sqlite3")
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)


class SettingsError(ValueError):
    """A present configuration file could not be parsed or validated."""


def default_data_dir() -> Path:
    """Return the per-user directory used for mutable application data."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "dlsite-organizer"


def default_config_path() -> Path:
    """Return the optional user configuration file location."""
    return default_data_dir() / "config.toml"


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
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise SettingsError(f"无法读取配置文件：{config_path}") from exc
