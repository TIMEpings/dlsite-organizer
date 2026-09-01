from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


def test_project_license_has_frozen_attribution() -> None:
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    legacy_attribution = "dlsite-organizer " + "contributors"

    assert "Copyright (c) 2026 TIMEpings" in license_text
    assert f"Copyright (c) 2026 {legacy_attribution}" not in license_text


def test_project_metadata_has_frozen_author() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as metadata_file:
        project = tomllib.load(metadata_file)["project"]
    legacy_attribution = "dlsite-organizer " + "contributors"

    assert project["authors"] == [{"name": "TIMEpings"}]
    assert legacy_attribution not in str(project.get("maintainers", ""))
