# Manual candidate review

v0.8 adds a local annotation layer for derived candidates. A review is an
append-only event on a canonical unordered RJ pair and is persisted with
`MANUAL_USER_REVIEW` provenance. Outcomes are `RELATED`, `NOT_RELATED`, and
`UNSURE`; only `RELATED` requires a relation type. Directional types store the
explicit subject and target selected by the user, while `BUNDLED_WITH`,
`OTHER`, and `UNKNOWN` are symmetric.

Each event stores a versioned evidence snapshot (maker identity, registration
date, RJ distance, local group counts, and candidate evaluation time). New
reviews of candidates produced by the discovery service use snapshot schema
v2 and include `policy_provenance`:

* `policy_id` identifies the discovery policy (`same-maker-same-date`), not an
  implementation class;
* `policy_version` is the manually maintained semantic version of that policy;
* `application_version` is the software version when the review snapshot was
  created.

Snapshots created by v0.8/v0.9 use schema v1. Their policy provenance is
unavailable, remains `None`, and is never inferred or rewritten. Snapshot
application version is review-time provenance; it is not a Git SHA. This
provenance is never rewritten when metadata changes. The latest event is the
application view; history remains available for future ground-truth analysis.
These labels are local user annotations, not official DLsite evidence. They do
not produce scores, probabilities, automatic confirmation, or model training.

## Evaluation dataset

The v0.9 `EvaluationDatasetService` reads this event history and derives one
latest label per canonical pair. It keeps all historical events intact,
excludes `UNSURE` from decided-label rates, and uses each event's review-time
evidence snapshot. A damaged latest snapshot is reported as invalid evidence,
without falling back to an older review or current metadata. Derived records
are exportable as a notes-free deterministic CSV and are not stored as a new
database table.

Evaluation records expose optional `candidate_policy_id`,
`candidate_policy_version`, and `application_version`. Summary statistics
report legacy/provenance-bearing counts and a descriptive latest-record
distribution by `(policy_id, policy_version)`. The legacy display group is
`LEGACY_UNKNOWN_POLICY`; its underlying fields remain empty. This is not a
policy performance comparison.

The local candidate review queue is an additional entry point for the same
manual review service. Persistence remains append-only, local-only, and uses
the existing review-time evidence snapshot contract.
