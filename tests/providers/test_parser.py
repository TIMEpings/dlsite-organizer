from pathlib import Path

import pytest

from dlsite_organizer.domain.work import Availability
from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError
from dlsite_organizer.providers.dlsite.parser import parse_product_page

FIXTURE = Path(__file__).parents[1] / "fixtures" / "product_semantic.html"


def test_parses_semantic_product_fixture() -> None:
    work = parse_product_page(
        FIXTURE.read_text(encoding="utf-8"),
        "RJ01609020",
        section="maniax",
    )

    assert work.workno == "RJ01609020"
    assert work.title == "雨音と過ごす夜"
    assert work.maker_id == "RG12345"
    assert work.maker_name == "星空サークル"
    assert work.release_date is not None and work.release_date.isoformat() == "2025-06-14"
    assert work.series_name == "雨音シリーズ"
    assert work.cvs == ["声優 花子", "声優 太郎"]
    assert work.tags == ["癒し", "バイノーラル"]
    assert work.cover_url == "https://img.dlsite.jp/sample.jpg"
    assert work.availability is Availability.AVAILABLE


def test_rejects_page_without_product_semantic_data() -> None:
    with pytest.raises(DlsiteParseError):
        parse_product_page(
            "<html><title>Access denied</title></html>",
            "RJ01609020",
            section="maniax",
        )


def test_optional_fields_degrade_without_failing() -> None:
    html = '<script type="application/ld+json">{"@type":"Product","name":"Only title"}</script>'
    work = parse_product_page(html, "RJ01609020", section="maniax")

    assert work.title == "Only title"
    assert work.maker_name is None
    assert work.release_date is None
    assert work.cover_url is None
