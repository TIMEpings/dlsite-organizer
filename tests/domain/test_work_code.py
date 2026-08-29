import pytest

from dlsite_organizer.domain.work_code import (
    WorkCode,
    WorkCodeError,
    extract_work_codes,
    normalize_rjcode,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RJ123456", "RJ123456"),
        ("rj01234567", "RJ01234567"),
        ("  RJ01609020  ", "RJ01609020"),
        ("RJ1234567890", "RJ1234567890"),
    ],
)
def test_normalizes_valid_rjcodes(raw: str, expected: str) -> None:
    assert normalize_rjcode(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "RJ12345", "RJ12345678901", "RJ12A456", "12345678", "RX123456"],
)
def test_rejects_invalid_or_out_of_range_rjcodes(raw: str) -> None:
    with pytest.raises(WorkCodeError):
        normalize_rjcode(raw)


def test_general_work_code_can_represent_future_supported_prefixes() -> None:
    assert str(WorkCode.parse("vj123456")) == "VJ123456"
    assert str(WorkCode.parse("BJ12345678")) == "BJ12345678"


@pytest.mark.parametrize(
    ("folder_name", "expected"),
    [
        ("RJ01609020", ["RJ01609020"]),
        ("[RJ01609020] title", ["RJ01609020"]),
        ("【RJ01609020】title", ["RJ01609020"]),
        ("Circle - rj01609020", ["RJ01609020"]),
        ("SomeWorkRJ01609020", ["RJ01609020"]),
        ("RJ01609020 [RJ01609020]", ["RJ01609020"]),
        ("RJ01609020 Something RJ01636949", ["RJ01609020", "RJ01636949"]),
        ("Misc", []),
    ],
)
def test_extract_work_codes_reuses_normalization(folder_name: str, expected: list[str]) -> None:
    assert extract_work_codes(folder_name) == expected
