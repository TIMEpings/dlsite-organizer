from datetime import date

import pytest

from dlsite_organizer.domain.naming import sanitize_windows_name
from dlsite_organizer.domain.work import AgeCategory, Work, WorkLanguage
from dlsite_organizer.services.naming import (
    CANONICAL_TEMPLATE_VARIABLES,
    DEFAULT_NAMING_TEMPLATE,
    NamingService,
    NamingTemplateError,
)


def test_formats_complete_metadata() -> None:
    work = Work(
        workno="RJ01234567",
        title="Work Title",
        maker_id="RG12345",
        maker_name="Circle Name",
    )
    assert NamingService().format(work) == "[RJ01234567][Circle Name]Work Title"


def test_default_template_placeholders_are_advertised_as_canonical() -> None:
    import string

    placeholders = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(DEFAULT_NAMING_TEMPLATE)
        if field_name is not None
    }
    advertised = {variable[1:-1] for _, variable in CANONICAL_TEMPLATE_VARIABLES}

    assert placeholders <= advertised
    assert "rjcode" not in advertised


def test_omits_missing_maker_group() -> None:
    work = Work(workno="RJ01234567", title="Work Title")
    assert NamingService().format(work) == "[RJ01234567]Work Title"
    assert "None" not in NamingService().format(work)


def test_preserves_unicode_and_japanese() -> None:
    work = Work(workno="RJ01234567", title="雨音の夜・中文标题", maker_name="星空サークル")
    assert NamingService().format(work) == "[RJ01234567][星空サークル]雨音の夜・中文标题"


@pytest.mark.parametrize("workno", ["RJ01234567", "BJ00000001", "VJ00000001"])
def test_workno_placeholder_renders_the_complete_canonical_code(workno: str) -> None:
    work = Work(workno=workno, title="Work Title")

    assert NamingService("{workno}").format(work) == workno


def test_new_default_template_is_the_fresh_profile_golden() -> None:
    work = Work(workno="BJ00000001", title="Work Title", maker_name="Circle Name")

    assert NamingService().format(work) == "[BJ00000001][Circle Name]Work Title"


def test_replaces_windows_illegal_characters_and_trailing_dot_space() -> None:
    assert sanitize_windows_name('a<b>c:d"e/f\\g|h?i* . ') == "a_b_c_d_e_f_g_h_i_"


def test_prefixes_windows_reserved_names() -> None:
    assert sanitize_windows_name("CON") == "_CON"
    assert sanitize_windows_name("lpt1.txt") == "_lpt1.txt"


def test_supports_rich_metadata_aliases_without_changing_default_template() -> None:
    work = Work(
        workno="RJ01234567",
        title="Work Title",
        maker_name="Circle Name",
        series_name="Series",
        cvs=["Alice", "Bob"],
        tags=["ASMR", "Healing"],
        age_category=AgeCategory.R15,
        language="ENG",
    )

    formatted = NamingService(
        "{rjcode}-{work_name}-{maker_id}-{maker_name}-{series_name}-"
        "{cv_list}-{cv_list_str}-{tags_list}-{tags_list_str}-{age_category}-{language}"
    ).format(work)

    assert formatted == (
        "RJ01234567-Work Title--Circle Name-Series-Alice, Bob-Alice, Bob-"
        "ASMR, Healing-ASMR, Healing-r15-ENG"
    )


def test_legacy_rjcode_alias_still_renders() -> None:
    work = Work(workno="RJ01234567", title="Work Title")

    assert NamingService("{rjcode} {title}").format(work) == "RJ01234567 Work Title"


def test_canonical_template_supports_configured_metadata_formatting() -> None:
    work = Work(
        workno="RJ01234567",
        title="Work Title",
        maker_name="Circle Name",
        series_name="Series",
        cvs=["Alice", "Bob"],
        tags=["ASMR", "Healing", "Sleep"],
        age_category=AgeCategory.R18,
        language=WorkLanguage.JPN,
        release_date=date(2026, 8, 31),
    )

    formatted = NamingService(
        "[{maker_name}][{rjcode}][{series}] {title} {cv} {tags} {age} {language} {release_date}",
        cv_separator=" / ",
        cv_prefix="(",
        cv_suffix=")",
        tag_separator=" ",
        max_tags=2,
        date_format="%Y%m%d",
    ).format(work)

    assert formatted == (
        "[Circle Name][RJ01234567][Series] Work Title (Alice _ Bob) ASMR Healing "
        "R18 日语 20260831"
    )


def test_missing_optional_fields_cleanup_and_unknown_language_are_safe() -> None:
    work = Work(workno="RJ01234567", title="Work Title", language="FUTURE")

    formatted = NamingService(
        "[{maker_name}][{series}] {title} [{cv}] {age} {language}"
    ).format(work)

    assert formatted == "Work Title FUTURE"
    assert "None" not in formatted


@pytest.mark.parametrize("template", ["", "   ", "{unknown_field}", "{title"])
def test_invalid_templates_are_rejected(template: str) -> None:
    with pytest.raises(NamingTemplateError):
        NamingService(template)
