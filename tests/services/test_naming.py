from dlsite_organizer.domain.naming import sanitize_windows_name
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.naming import NamingService


def test_formats_complete_metadata() -> None:
    work = Work(
        workno="RJ01234567",
        title="Work Title",
        maker_id="RG12345",
        maker_name="Circle Name",
    )
    assert NamingService().format(work) == "[Circle Name][RJ01234567] Work Title"


def test_omits_missing_maker_group() -> None:
    work = Work(workno="RJ01234567", title="Work Title")
    assert NamingService().format(work) == "[RJ01234567] Work Title"
    assert "None" not in NamingService().format(work)


def test_preserves_unicode_and_japanese() -> None:
    work = Work(workno="RJ01234567", title="雨音の夜・中文标题", maker_name="星空サークル")
    assert NamingService().format(work) == "[星空サークル][RJ01234567] 雨音の夜・中文标题"


def test_replaces_windows_illegal_characters_and_trailing_dot_space() -> None:
    assert sanitize_windows_name('a<b>c:d"e/f\\g|h?i* . ') == "a_b_c_d_e_f_g_h_i_"


def test_prefixes_windows_reserved_names() -> None:
    assert sanitize_windows_name("CON") == "_CON"
    assert sanitize_windows_name("lpt1.txt") == "_lpt1.txt"
