import json
from datetime import datetime
from pathlib import Path

import pytest

from dlsite_organizer.domain.work import AgeCategory, Work, WorkLanguage
from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError
from dlsite_organizer.providers.dlsite.sources import (
    merge_product_metadata,
    normalize_product_info_ajax,
    parse_product_info_ajax,
    parse_product_metadata,
    translation_attribution,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "dlsite"


def fixture_payload(workno: str) -> str:
    return (FIXTURE_DIR / f"product_info_{workno}.json").read_text(encoding="utf-8")


def rich_fixture_payload(workno: str) -> str:
    return (FIXTURE_DIR / f"product_metadata_{workno}.json").read_text(encoding="utf-8")


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


def test_product_metadata_source_maps_rich_fields_from_minimized_live_fixture() -> None:
    source = parse_product_metadata(rich_fixture_payload("RJ01609020"), "RJ01609020")
    work = source

    assert work.maker_identity == ("RG01058997", "のりプロ")
    assert work.cv_names == ("佃煮のりお",)
    assert len(work.tag_names or ()) == 6
    assert work.normalized_age_category is AgeCategory.GENERAL
    assert work.language_for("RJ01609020") is WorkLanguage.JPN
    assert work.cover_url is not None and work.cover_url.endswith("RJ01609020_img_main.jpg")


def test_product_metadata_rejects_invalid_core_workno_as_parse_error() -> None:
    payload = json.dumps(
        [{"workno": "not-a-workno", "work_name": "中性测试作品"}]
    )

    with pytest.raises(DlsiteParseError, match="malformed core metadata"):
        parse_product_metadata(payload, "RJ01609020")


def test_product_metadata_merge_keeps_child_maker_as_translation_attribution() -> None:
    original_ajax = parse_product_info_ajax(fixture_payload("RJ01609020"), "RJ01609020")
    child_ajax = parse_product_info_ajax(fixture_payload("RJ01637033"), "RJ01637033")
    original_rich = parse_product_metadata(rich_fixture_payload("RJ01609020"), "RJ01609020")
    child_rich = parse_product_metadata(rich_fixture_payload("RJ01637033"), "RJ01637033")

    child_work = merge_product_metadata(
        normalize_product_info_ajax(child_ajax, section="maniax"),
        child_rich,
        translation_info=child_ajax.translation_info,
        original_rich=original_rich,
    )

    assert child_work.maker_id == "RG01058997"
    assert child_work.maker_name == "のりプロ"
    assert child_work.language is WorkLanguage.CHI_HANS
    assert child_rich.language_for("RJ01637033") is None
    assert child_work.age_category is AgeCategory.GENERAL
    attribution = translation_attribution(child_ajax, child_rich, original_rich)
    assert attribution is not None
    assert attribution.maker_id == "RG01001331"
    assert attribution.maker_name == "MYHONYAKU"
    assert (child_work.maker_id, child_work.maker_name) != (
        attribution.maker_id,
        attribution.maker_name,
    )
    assert original_ajax.translation_info is not None


def test_translation_attribution_falls_back_to_core_when_rich_maker_is_missing() -> None:
    child_ajax = parse_product_info_ajax(fixture_payload("RJ01637033"), "RJ01637033")
    child_rich = parse_product_metadata(
        json.dumps(
            [{"workno": "RJ01637033", "work_name": "中性测试作品"}]
        ),
        "RJ01637033",
    )

    attribution = translation_attribution(child_ajax, child_rich, None)

    assert attribution is not None
    assert attribution.maker_id == "RG01001331"
    assert attribution.maker_name == "MYHONYAKU"


def test_product_metadata_merge_fills_descriptive_fields_but_keeps_core_authority() -> None:
    core = Work(
        workno="RJ01234567",
        title="核心标题",
        maker_id="RG-core",
        maker_name="核心社团",
        release_date=datetime(2026, 8, 1).date(),
        regist_datetime=datetime(2026, 8, 1, 2, 3, 4),
        cover_url="https://img.example.test/core.jpg",
        age_category=AgeCategory.GENERAL,
    )
    rich = parse_product_metadata(
        json.dumps(
            [
                {
                    "workno": "RJ01234567",
                    "work_name": "rich title must not win",
                    "maker_id": "RG-rich",
                    "maker_name": "丰富社团",
                    "series_name": "丰富系列",
                    "age_category": 2,
                    "creaters": {"voice_by": [{"name": "CV 丰富"}]},
                    "genres": [{"name": "标签丰富"}],
                    "language_editions": [
                        {"workno": "RJ01234567", "lang": "ENG"}
                    ],
                    "regist_date": "2026-08-02 00:00:00",
                    "image_main": {"url": "https://img.example.test/rich.jpg"},
                }
            ]
        ),
        "RJ01234567",
    )

    merged = merge_product_metadata(core, rich)

    assert merged.title == "核心标题"
    assert merged.maker_id == "RG-rich"
    assert merged.maker_name == "丰富社团"
    assert merged.series_name == "丰富系列"
    assert merged.cvs == ["CV 丰富"]
    assert merged.tags == ["标签丰富"]
    assert merged.language is WorkLanguage.ENG
    assert merged.age_category is AgeCategory.R15
    assert merged.regist_datetime == core.regist_datetime
    assert merged.cover_url == core.cover_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, AgeCategory.GENERAL),
        (2, AgeCategory.R15),
        (3, AgeCategory.R18),
        ("future", AgeCategory.UNKNOWN),
    ],
)
def test_product_metadata_age_and_optional_fields_fail_soft(
    raw: object,
    expected: AgeCategory,
) -> None:
    payload = [
        {
            "workno": "RJ01609020",
            "work_name": "中性测试作品",
            "age_category": raw,
            "language_editions": [{"workno": "RJ01609020", "lang": "future_lang"}],
            "genres": [{"name": "标签"}, {"name": None}, "malformed"],
            "creaters": {"voice_by": [{"name": "CV"}, "malformed"]},
            "regist_date": "not-a-date",
            "image_main": "malformed",
        }
    ]

    source = parse_product_metadata(json.dumps(payload), "RJ01609020")

    assert source.normalized_age_category is expected
    assert source.language_for("RJ01609020") == "FUTURE_LANG"
    assert source.tag_names == ("标签",)
    assert source.cv_names == ("CV",)
    assert source.release_date is None
    assert source.cover_url is None
