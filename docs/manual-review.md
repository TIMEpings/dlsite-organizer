# Manual candidate review

v0.10.2 extends the local annotation taxonomy without changing candidate
discovery. A review is an
append-only event on a canonical unordered RJ pair and is persisted with
`MANUAL_USER_REVIEW` provenance. Outcomes are `RELATED`, `NOT_RELATED`, and
`UNSURE`; only `RELATED` requires a relation type.

## Relation taxonomy

The complete manual relation taxonomy is:

| Type | Direction | Meaning |
| --- | --- | --- |
| `TRANSLATION_OF` | directional | Subject is translated from target; use only when the source/target direction is known. |
| `BONUS_OF` | directional | Subject is a bonus associated with target. |
| `LIMITED_BONUS_OF` | directional | Subject is a limited/edition bonus associated with target. |
| `CHILD_OF` | directional | Subject is a child work of target. |
| `INCLUDED_IN` | directional | Independent work subject is included in collection/package target. |
| `BUNDLED_WITH` | symmetric | Two works are in a symmetric bundle/set and containment is unknown or intentionally unexpressed. |
| `OTHER` | symmetric | Related, but not covered by a more specific taxonomy value. |
| `UNKNOWN` | symmetric | Related, but the relation type is not known. |
| `SAME_SERIES` | symmetric | Two independently released works explicitly belong to the same series, with no more specific relation. |
| `SAME_WORK_VARIANT` | symmetric | The same underlying work has different non-language versions/editions, such as a revised or format variant. |
| `SAME_WORK_LANGUAGE_VARIANT` | symmetric | The same underlying work has official different-language editions, without asserting translation direction. |

`SAME_SERIES` is not `SAME_WORK_VARIANT`: the former describes distinct works
within one series, while the latter describes different versions of one
underlying work. `SAME_WORK_VARIANT` is for non-language version/edition
differences. Use `SAME_WORK_LANGUAGE_VARIANT` for official language editions
when the reviewer does not want to assert which one translated from which.

`TRANSLATION_OF` remains directional. For example, `B TRANSLATION_OF A`
requires a clear statement that B was translated from A; different-language
titles alone are not enough.

`INCLUDED_IN` is also directional: an independently released work A stored as
`A INCLUDED_IN B` means collection/package B contains A. The UI may phrase this
as “A 收录于 B” or, when viewing the inverse work, “B 包含 A”, but there is no
separate `CONTAINS` enum and no second event. `BUNDLED_WITH` remains available
for genuinely symmetric bundle relationships; existing values are not
reinterpreted automatically.

Symmetric relations persist with `subject_workno = None` and
`target_workno = None`. Directional relations persist the explicit subject and
target selected by the reviewer. Canonical pair ordering never changes an
`INCLUDED_IN` direction.

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
the existing review-time evidence snapshot contract. Re-reviewing a pair appends
a new event; it never rewrites the old relation type or evidence snapshot. Thus
an `OTHER` event followed by `SAME_SERIES`, or a `BUNDLED_WITH` event followed by
`INCLUDED_IN`, remains a two-event history and only the latest event is used by
the current evaluation view.
