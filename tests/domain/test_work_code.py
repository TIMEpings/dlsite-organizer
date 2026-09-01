import pytest

from dlsite_organizer.domain.work_code import (
    WorkCode,
    WorkCodeError,
    extract_work_codes,
    normalize_rjcode,
    normalize_workno,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RJ123456", "RJ123456"),
        ("rj01234567", "RJ01234567"),
        ("  RJ01609020  ", "RJ01609020"),
        ("RJ1234567890", "RJ1234567890"),
        ("bj00000001", "BJ00000001"),
        ("VJ009933", "VJ009933"),
        ("vj00000001", "VJ00000001"),
    ],
)
def test_normalizes_valid_work_codes(raw: str, expected: str) -> None:
    assert normalize_workno(raw) == expected


def test_legacy_rjcode_normalizer_uses_the_general_work_code_contract() -> None:
    assert normalize_rjcode("bj00000001") == "BJ00000001"
    assert normalize_rjcode("vj00000001") == "VJ00000001"


@pytest.mark.parametrize(
    "raw",
    ["", "RJ12345", "RJ12345678901", "RJ12A456", "12345678", "RX123456"],
)
def test_rejects_invalid_or_out_of_range_work_codes(raw: str) -> None:
    with pytest.raises(WorkCodeError):
        normalize_workno(raw)


def test_work_code_rejects_unsupported_prefix_and_malformed_number() -> None:
    for raw in ("RX123456", "R123456", "RJ12A456", "RJ12345", "RJ12345678901"):
        with pytest.raises(WorkCodeError):
            WorkCode.parse(raw)


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
        ("book BJ00000001", ["BJ00000001"]),
        ("software vj009933", ["VJ009933"]),
        ("BJ00000001 [bj00000001]", ["BJ00000001"]),
        ("RJ01609020 + BJ00000001", ["RJ01609020", "BJ00000001"]),
        ("BJ00000001 + VJ00000001", ["BJ00000001", "VJ00000001"]),
        ("Misc", []),
    ],
)
def test_extract_work_codes_reuses_normalization(folder_name: str, expected: list[str]) -> None:
    assert extract_work_codes(folder_name) == expected
