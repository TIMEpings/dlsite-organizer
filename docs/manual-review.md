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
