from __future__ import annotations

import struct
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
PNG_PATH = PROJECT_ROOT / "assets" / "branding" / "app_icon.png"
ICO_PATH = PROJECT_ROOT / "assets" / "branding" / "app_icon.ico"
EXPECTED_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def test_authoritative_png_and_multi_resolution_ico_are_committed() -> None:
    png = PNG_PATH.read_bytes()
    ico = ICO_PATH.read_bytes()

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert int.from_bytes(png[16:20], "big") == 1254
    assert int.from_bytes(png[20:24], "big") == 1254

    reserved, image_type, count = struct.unpack_from("<HHH", ico, 0)
    assert (reserved, image_type, count) == (0, 1, len(EXPECTED_SIZES))

    sizes: list[int] = []
    for index in range(count):
        width, height, _colors, _reserved, _planes, _bits, length, offset = (
            struct.unpack_from("<BBBBHHII", ico, 6 + (index * 16))
        )
        sizes.append(256 if width == 0 else width)
        assert height == (0 if width == 0 else width)
        assert ico[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert length > 0

    assert tuple(sizes) == EXPECTED_SIZES
