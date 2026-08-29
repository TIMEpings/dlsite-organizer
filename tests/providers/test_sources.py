import json
from pathlib import Path

import pytest

from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError
from dlsite_organizer.providers.dlsite.sources import (
    normalize_product_info_ajax,
    parse_product_info_ajax,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "product_info_ajax_contract.json"


def fixture_payload() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_product_info_ajax_maps_core_metadata_and_tolerates_unknown_fields() -> None:
    source = parse_product_info_ajax(fixture_payload(), "RJ01609020")
    work = normalize_product_info_ajax(source, section="maniax")

    assert work.workno == "RJ01609020"
    assert work.title == "雨音と過ごす夜"
    assert work.maker_id == "RG12345"
    assert work.maker_name == "星空サークル"
    assert work.release_date is not None and work.release_date.isoformat() == "2025-06-14"
    assert work.cover_url == "https://img.dlsite.jp/sample.jpg"
    assert source.work_type == "SOU"
    assert source.age_category == 3


def test_product_info_ajax_allows_missing_optional_fields() -> None:
    source = parse_product_info_ajax(
        json.dumps({"workno": "RJ01609020", "work_name": "Only title"}),
        "RJ01609020",
    )

    assert source.maker_id is None
    assert source.maker_name is None
    assert source.regist_date is None
    assert source.translation_info is None
    assert normalize_product_info_ajax(source, section="maniax").release_date is None


@pytest.mark.parametrize(
    "payload",
    [
        {"workno": "RJ01609020"},
        {"workno": "RJ01609020", "work_name": 123},
        {"workno": 123, "work_name": "Title"},
    ],
)
def test_product_info_ajax_rejects_malformed_required_core_fields(payload: object) -> None:
    with pytest.raises(DlsiteParseError, match="malformed core metadata"):
        parse_product_info_ajax(json.dumps(payload), "RJ01609020")


def test_product_info_ajax_rejects_workno_mismatch() -> None:
    with pytest.raises(DlsiteParseError, match="workno mismatch"):
        parse_product_info_ajax(
            json.dumps({"workno": "RJ09999999", "work_name": "Other work"}),
            "RJ01609020",
        )


def test_translation_info_is_absent_when_source_omits_it() -> None:
    source = parse_product_info_ajax(
        json.dumps({"workno": "RJ01609020", "work_name": "Only title"}),
        "RJ01609020",
    )
    assert source.translation_info is None


def test_translation_info_preserves_provider_contract_without_creating_relations() -> None:
    source = parse_product_info_ajax(fixture_payload(), "RJ01609020")
    assert source.translation_info is not None
    assert source.translation_info.original_workno == "RJ01000001"
    assert source.translation_info.parent_workno == "RJ01000002"
    assert source.translation_info.child_worknos == ["RJ02000001", "RJ02000002"]
    assert source.translation_info.lang == "zh_CN"
