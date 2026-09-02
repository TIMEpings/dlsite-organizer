"""Small isolated subprocess harness for single-instance application tests."""

from __future__ import annotations

import os
from pathlib import Path

import dlsite_organizer.__main__ as application_main
from dlsite_organizer.persistence.database import Database


def _mark(suffix: str) -> None:
    marker = os.environ.get("DLSITE_ORGANIZER_TEST_MARKER")
    if not marker:
        return
    path = Path(marker).with_name(f"{Path(marker).name}.{suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="ascii")


original_build_components = application_main.build_components


def tracked_build_components(settings):
    _mark("components")
    components = original_build_components(settings)
    service = components.quick_rename_service
    original_rename = service.rename

    def tracked_rename(*args, **kwargs):
        _mark("quick")
        return original_rename(*args, **kwargs)

    service.rename = tracked_rename  # type: ignore[method-assign]
    return components


application_main.build_components = tracked_build_components  # type: ignore[assignment]

original_database_init = Database.__init__


def tracked_database_init(self, path):
    _mark("database")
    original_database_init(self, path)


Database.__init__ = tracked_database_init  # type: ignore[method-assign]

original_listen = application_main.InstanceCoordinator.listen


def tracked_listen(coordinator):
    result = original_listen(coordinator)
    if result:
        _mark("ready")
    return result


application_main.InstanceCoordinator.listen = tracked_listen  # type: ignore[method-assign]
application_main._schedule_startup_smoke = lambda *_args: None


if __name__ == "__main__":
    raise SystemExit(application_main.main())
