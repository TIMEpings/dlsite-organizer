"""Historical RJ offsets from 494 known bonus pairs in 2026."""

from bisect import bisect_left
from math import log1p

# Aggregates from known pairs only. Positive percentages are cumulative shares
# of all 494 pairs. One additional pair is at -18,545; the sample can
# overrepresent discoverable bonuses and does not estimate actual discovery.
_OFFSETS = (
    0, 10, 20, 50, 100, 500, 1000, 5000, 10_000, 20_000, 40_000,
    50_000, 60_000, 80_000, 100_000, 120_000, 140_000, 165_701, 172_074,
)
_PERCENTAGES = (
    0., 10.93, 20.04, 28.54, 30.77, 33.20, 34.41, 49.19, 57.29, 72.47,
    91.30, 95.55, 97.37, 98.18, 98.79, 98.99, 99.39, 99.60, 99.80,
)


MAX_COVERAGE_OFFSET = _OFFSETS[-1]
MIN_KNOWN_NEGATIVE_OFFSET = -18_545


def _cumulative(offset: int) -> float:
    if offset >= MAX_COVERAGE_OFFSET:
        return _PERCENTAGES[-1]
    right = bisect_left(_OFFSETS, offset)
    if offset == _OFFSETS[right]:
        return _PERCENTAGES[right]
    left = right - 1
    lower, upper = log1p(_OFFSETS[left]), log1p(_OFFSETS[right])
    fraction = (log1p(offset) - lower) / (upper - lower)
    return _PERCENTAGES[left] + fraction * (_PERCENTAGES[right] - _PERCENTAGES[left])


def estimate_bonus_coverage(offsets: tuple[int, int]) -> float | None:
    """Estimate historical coverage of the positive portion of [first, last]."""
    first, last = offsets
    if first > last:
        return None
    if first <= MIN_KNOWN_NEGATIVE_OFFSET and last >= MAX_COVERAGE_OFFSET:
        return 100.0
    if last < 1:
        return None
    return _cumulative(last) - _cumulative(max(0, first - 1))
