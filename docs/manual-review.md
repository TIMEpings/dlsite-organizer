# Manual candidate review

v0.8 adds a local annotation layer for derived candidates. A review is an
append-only event on a canonical unordered RJ pair and is persisted with
`MANUAL_USER_REVIEW` provenance. Outcomes are `RELATED`, `NOT_RELATED`, and
`UNSURE`; only `RELATED` requires a relation type. Directional types store the
explicit subject and target selected by the user, while `BUNDLED_WITH`,
`OTHER`, and `UNKNOWN` are symmetric.

Each event stores a versioned evidence snapshot (maker identity, registration
date, RJ distance, local group counts, and candidate evaluation time). This is
review-time provenance and is never rewritten when metadata changes. The
latest event is the application view; history remains available for future
ground-truth analysis. These labels are local user annotations, not official
DLsite evidence. They do not produce scores, probabilities, automatic
confirmation, or model training.

## Evaluation dataset

The v0.9 `EvaluationDatasetService` reads this event history and derives one
latest label per canonical pair. It keeps all historical events intact,
excludes `UNSURE` from decided-label rates, and uses each event's review-time
evidence snapshot. A damaged latest snapshot is reported as invalid evidence,
without falling back to an older review or current metadata. Derived records
are exportable as a notes-free deterministic CSV and are not stored as a new
database table.
