"""Process-local serialization for high-level filesystem mutations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock


class MutationGate:
    """Serialize complete mutation transactions within one application process.

    The gate deliberately knows nothing about the filesystem or journal.  Its
    caller owns the full transaction boundary, including validation and all
    journal state transitions that surround a filesystem mutation.
    """

    def __init__(self) -> None:
        self._lock = Lock()

    @contextmanager
    def acquire(self) -> Iterator[None]:
        """Hold exclusive mutation ownership until the surrounding operation ends."""
        with self._lock:
            yield


_SHARED_MUTATION_GATE = MutationGate()


def shared_mutation_gate() -> MutationGate:
    """Return the process-wide fallback gate used outside the composition root."""
    return _SHARED_MUTATION_GATE
