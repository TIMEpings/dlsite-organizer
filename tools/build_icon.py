"""Build the committed Windows ICO from the authoritative PNG source asset.

This is a build-time helper.  It uses PySide6's high-quality image scaling,
which is already present in the packaging environment, and emits PNG-backed
ICO entries so the alpha channel is retained at every requested size.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage

ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Could not open the in-memory PNG buffer")
    try:
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Could not encode a resized icon image as PNG")
        return bytes(buffer.data())
    finally:
        buffer.close()


def build_ico(source: Path, destination: Path) -> tuple[int, ...]:
    """Resize ``source`` and write a multi-resolution PNG-backed ICO."""
    source_image = QImage(str(source))
    if source_image.isNull():
        raise ValueError(f"Could not load source PNG: {source}")
    if source_image.width() != source_image.height():
        raise ValueError("Application icon source must be square")
    if not source_image.hasAlphaChannel():
        raise ValueError("Application icon source must retain transparency")

    entries: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        resized = source_image.scaled(
            QSize(size, size),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        entries.append((size, _png_bytes(resized)))

    header_size = 6 + (16 * len(entries))
    offset = header_size
    directory = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    image_data = bytearray()
    for size, payload in entries:
        encoded_dimension = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_dimension,
                encoded_dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        image_data.extend(payload)
        offset += len(payload)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(directory + image_data)
    return tuple(size for size, _payload in entries)


def _parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=project_root / "assets" / "branding" / "app_icon.png",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "assets" / "branding" / "app_icon.ico",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    sizes = build_ico(arguments.source, arguments.output)
    print(f"Built {arguments.output} with sizes: {', '.join(map(str, sizes))}")
