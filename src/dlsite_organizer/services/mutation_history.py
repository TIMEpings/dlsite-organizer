"""Shared callback seam for journal-backed mutation state changes."""

from __future__ import annotations

import logging
from collections.abc import Callable

MutationHistoryChanged = Callable[[], None]


def notify_mutation_history_changed(
    callback: MutationHistoryChanged | None,
    *,
    logger: logging.Logger,
) -> None:
    """Notify runtime consumers without allowing UI callbacks to affect a mutation."""
    if callback is None:
        return
    try:
        callback()
    except Exception:
        logger.exception("Mutation history notification failed")
