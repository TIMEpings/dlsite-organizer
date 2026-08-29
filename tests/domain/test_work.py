from datetime import date

import pytest
from pydantic import ValidationError

from dlsite_organizer.domain.work import Availability, Work


def test_work_normalizes_required_and_optional_fields() -> None:
    work = Work(
        workno="rj01234567",
        title="  日本語タイトル  ",
        maker_id=" RG12345 ",
        maker_name=" サークル ",
        release_date=date(2025, 1, 2),
        availability=Availability.AVAILABLE,
    )

    assert work.workno == "RJ01234567"
    assert work.title == "日本語タイトル"
    assert work.maker_id == "RG12345"
    assert work.maker_name == "サークル"


def test_work_requires_title_and_workno() -> None:
    with pytest.raises(ValidationError):
        Work(workno="RJ01234567", title="   ")


def test_work_list_defaults_are_not_shared() -> None:
    first = Work(workno="RJ01234567", title="First")
    second = Work(workno="RJ01234568", title="Second")

    assert first.cvs is not second.cvs
    assert first.tags is not second.tags
    assert first.cvs == second.cvs == []


def test_work_deduplicates_list_values_preserving_order() -> None:
    work = Work(
        workno="RJ01234567",
        title="Title",
        cvs=["Alice", " Alice ", "Bob", ""],
        tags=["音声", "音声", " 癒し "],
    )

    assert work.cvs == ["Alice", "Bob"]
    assert work.tags == ["音声", "癒し"]
