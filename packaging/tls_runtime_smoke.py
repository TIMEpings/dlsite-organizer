"""Verify TLS resource initialization inside a frozen Python runtime."""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import httpx


def main() -> int:
    """Check the packaged CA path and normal verified client defaults."""
    bundle = Path(certifi.where())
    if not bundle.is_file():
        raise RuntimeError(f"certifi.where() does not resolve to a file: {bundle}")

    context = ssl.create_default_context(cafile=str(bundle))
    if context.verify_mode is not ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("default SSL context is not configured for certificate verification")

    with httpx.Client() as client:
        if client is None:  # pragma: no cover - documents the smoke assertion
            raise RuntimeError("httpx client initialization returned no client")

    print(f"certifi.where(): {bundle}")
    print(f"certifi bundle bytes: {bundle.stat().st_size}")
    print("ssl.create_default_context: PASS")
    print("httpx.Client: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
