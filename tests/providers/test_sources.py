import json
from datetime import datetime
from pathlib import Path

import pytest

from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError
from dlsite_organizer.providers.dlsite.sources import (
    normalize_product_info_ajax,
    parse_product_info_ajax,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "dlsite"


def fixture_payload(workno: str) -> str:
    return (FIXTURE_DIR / f"product_info_{workno}.json").read_text(encoding="utf-8")


def test_real_original_response_maps_metadata_and_preserves_regist_datetime() -> None:
    source = parse_product_info_ajax(fixture_payload("RJ01609020"), "rj01609020")
    work = normalize_product_info_ajax(source, section="maniax")

    assert source.requested_workno == "RJ01609020"
    assert source.envelope_workno == "RJ01609020"
    assert source.product_id is None
    assert source.bonuses == []
    assert source.bonus_evidence is not None
    assert source.bonus_evidence.entries == ()
    assert source.maker_id == "RG01058997"
    assert source.maker_name is None
    assert source.regist_datetime == datetime(2026, 5, 26)
    assert work.workno == "RJ01609020"
    assert work.release_date is not None and work.release_date.isoformat() == "2026-05-26"
    assert work.cover_url == (
        "https://img.dlsite.jp/modpub/images2/work/doujin/"
        "RJ01610000/RJ01609020_img_main.jpg"
    )

    assert source.translation_info is not None
    assert source.translation_info.is_translation_agree is True
    assert source.translation_info.is_volunteer is False
    assert source.translation_info.is_original is True
    assert source.translation_info.is_parent is False
    assert source.translation_info.is_child is False
    assert source.translation_info.original_workno is None
    assert source.translation_info.parent_workno is None
    assert source.translation_info.child_worknos == []
    assert source.translation_info.lang is None


def test_real_parent_response_preserves_translation_topology() -> None:
    source = parse_product_info_ajax(fixture_payload("RJ01636949"), "RJ01636949")

    assert source.maker_id == "RG60289"
    assert source.maker_name is None
    assert source.regist_datetime == datetime(2026, 7, 7)
    assert source.translation_info is not None
    assert source.translation_info.is_original is False
    assert source.translation_info.is_parent is True
    assert source.translation_info.is_child is False
    assert source.translation_info.original_workno == "RJ01609020"
    assert source.translation_info.parent_workno is None
    assert source.translation_info.child_worknos == ["RJ01637033", "RJ01636950", "RJ01663275"]
    assert source.translation_info.lang == "CHI_HANS"


def test_real_child_response_checks_metadata_identity_and_translation_topology() -> None:
    source = parse_product_info_ajax(fixture_payload("RJ01637033"), "RJ01637033")

    assert source.envelope_workno == "RJ01637033"
    assert source.product_id == "RJ01637033"
    assert source.maker_id == "RG01001331"
    assert source.maker_name == "MYHONYAKU"
    assert source.translation_info is not None
    assert source.translation_info.is_original is False
    assert source.translation_info.is_parent is False
    assert source.translation_info.is_child is True
    assert source.translation_info.original_workno == "RJ01609020"
    assert source.translation_info.parent_workno == "RJ01636949"
    assert source.translation_info.child_worknos == []
    assert source.translation_info.lang == "CHI_HANS"


def test_product_info_ajax_rejects_missing_requested_envelope_key() -> None:
    with pytest.raises(DlsiteParseError, match="did not contain requested key RJ01636949"):
        parse_product_info_ajax(fixture_payload("RJ01609020"), "RJ01636949")


def test_product_info_ajax_rejects_unexpected_extra_envelope_key() -> None:
    payload = json.loads(fixture_payload("RJ01609020"))
    payload["RJ09999999"] = payload["RJ01609020"]

    with pytest.raises(DlsiteParseError, match="unexpected top-level key"):
        parse_product_info_ajax(json.dumps(payload), "RJ01609020")


def test_product_info_ajax_rejects_inconsistent_metadata_product_id() -> None:
    payload = json.loads(fixture_payload("RJ01637033"))
    payload["RJ01637033"]["product_id"] = "RJ09999999"

    with pytest.raises(DlsiteParseError, match="product_id mismatch"):
        parse_product_info_ajax(json.dumps(payload), "RJ01637033")


def test_product_info_ajax_allows_missing_optional_fields_in_a_valid_envelope() -> None:
    source = parse_product_info_ajax(
        json.dumps({"RJ01609020": {"work_name": "Only title"}}),
        "RJ01609020",
    )

    assert source.maker_id is None
    assert source.maker_name is None
    assert source.regist_datetime is None
    assert source.translation_info is None
    assert normalize_product_info_ajax(source, section="maniax").release_date is None


@pytest.mark.parametrize(
    "payload",
    [
        {"RJ01609020": {}},
        {"RJ01609020": {"work_name": 123}},
        {"RJ01609020": {"work_name": "Title", "product_id": 123}},
    ],
)
def test_product_info_ajax_rejects_malformed_required_core_fields(payload: object) -> None:
    with pytest.raises(DlsiteParseError, match="malformed core metadata"):
        parse_product_info_ajax(json.dumps(payload), "RJ01609020")
