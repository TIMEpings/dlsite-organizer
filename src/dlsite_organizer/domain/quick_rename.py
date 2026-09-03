"""Business rules shared by every Quick Rename entry point."""

from __future__ import annotations

MAX_QUICK_RENAME_ITEMS = 32


def quick_rename_batch_limit_message() -> str:
    """Return the one user-facing message for an oversized Quick Rename batch."""
    return f"一次最多处理 {MAX_QUICK_RENAME_ITEMS} 个文件夹。"
