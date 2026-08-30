# Manual review evaluation dataset

v0.9 derives an evaluation dataset from the append-only
`manual_relation_reviews` history. The default evaluation policy is version 1:
one canonical unordered pair contributes its latest valid manual review, chosen
by `reviewed_at` and then event `id`. The source of truth remains the review
events; evaluation records and summaries are rebuilt in memory and are not
persisted.

Labels are `RELATED`, `NOT_RELATED`, and `UNSURE`. `RELATED` and `NOT_RELATED`
are the decided denominator. `UNSURE` is never treated as negative. A pair
without a review is not an evaluation row and is not a `NO_REVIEW` negative.
`manual_related_rate` is `RELATED / (RELATED + NOT_RELATED)` and always carries
the decided count. A configurable default minimum of 20 decided labels is only
a presentation guard: below it the metric state is `INSUFFICIENT_LABELS`; it
does not provide statistical significance or confidence.

Evidence comes only from the immutable review-time
`CandidateEvidenceSnapshot`, never from current metadata. If the latest label
is readable but its snapshot is damaged, the record is `INVALID_SNAPSHOT`: its
manual outcome counts remain included, but evidence-group denominators exclude
it. The service preserves canonical `workno_a`/`workno_b` and directional
`subject_workno`/`target_workno`. Relation type counts include concrete manual
types; `UNKNOWN` and `OTHER` remain generic related labels. A bonus subset is
only a count/list of manually labelled `BONUS_OF` and `LIMITED_BONUS_OF` pairs;
it is not bonus accuracy.

Schema v2 snapshots expose candidate discovery provenance through
`candidate_policy_id`, `candidate_policy_version`, and `application_version` on
each `EvaluationRecord`. Schema v1 snapshots expose `None` for all three. The
summary reports the number of legacy and provenance-bearing latest records and
groups valid records by `(policy_id, policy_version)` for descriptive
distribution only. Schema v1 records use the display key
`LEGACY_UNKNOWN_POLICY`; this does not infer a historical policy.

CSV export is latest-only, deterministic, UTF-8 with BOM for spreadsheet
compatibility, and excludes review notes by default. Export does not mutate the
database.

These are local user annotations, not official DLsite truth. Reviewed samples
are selected by the user and therefore can have selection bias. The default
candidate discovery policy (`same maker + same registration date`) also limits
the covered recall space; the dataset does not evaluate unobserved policies.
There are no confidence intervals, random sampling, automatic learning, score,
probability, policy tuning, or automatic confirmation. Older snapshots do not
carry policy provenance, so historical policy grouping is a known limitation.
CSV export adds the deterministic columns `snapshot_schema_version`,
`candidate_policy_id`, `candidate_policy_version`, and `application_version`;
v1 rows leave the provenance columns empty. The `manual_relation_reviews` table
is unchanged and stored snapshot JSON is never migrated.

## v0.10 queue selection bias

The local candidate review queue continues to select pairs using
`same-maker-same-date` policy version 1. Labels entered from that queue are
therefore subject to the same candidate-policy selection bias as lookup
reviews. The queue writes no evaluation rows or metrics; rebuilding the
evaluation dataset consumes the append-only manual review events.
