# Local Candidate Review Queue

The v0.10 review queue is a derived, local-only view over the current
`KnownWorkSnapshot` universe. It is rebuilt on refresh and is never persisted
as candidate rows or queue history. Refresh performs no provider/network calls.

The queue reuses `same-maker-same-date` policy version `1` and the shared
candidate evidence evaluator. Pairs are generated inside maker-identity and
registration-date groups, canonicalized (`A-B == B-A`), and ordered for
presentation by registration date, maker, RJ numeric distance, and work
numbers. Ordering is not a probability or confidence ranking.

Filters are Unreviewed, Unsure, Reviewed, All, Related, and Not related.
Latest append-only manual review events determine state; queue appearance is
never a label. Reviewing a row uses the existing manual review service and
stores the same v2 evidence snapshot with policy provenance. Evaluation data
is rebuilt later from those review events; the queue does not mutate metrics or
evaluation records.

The default filter is Unreviewed. The result reports known-work, eligible-work,
filtered pair, returned-row, and descriptive counts for each latest review
state. The UI uses a page size of 100 and resets to the first page when the
filter changes. Empty metadata, no eligible combinations, and an exhausted
Unreviewed view have distinct messages.

Only local current-cache or valid historical metadata is used. Explicit current
and historical confirmed translation pairs are excluded when they can be
reconstructed by the existing relation services.
