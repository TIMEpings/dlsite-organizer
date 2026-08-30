# v0.7 Candidate Evidence

The application keeps three semantic layers separate:

* current confirmed relations from the current provider response;
* historical confirmed relations reconstructed from persisted observations;
* derived `CandidateRelation` values generated from the local known-work universe.

Candidates are explainable review suggestions, never confirmed facts. They are
not persisted and carry no score, probability, confidence percentage, or bonus
classification. Current and historical confirmed pairs are filtered out.

## Evidence and context

The evaluator emits exact maker identity (`maker_id`, or conservative exact
NFKC-normalized maker name when both IDs are absent), same registration date,
and raw numeric RJ distance. Local same-maker/day group size and local known
maker population are context only; they are not negative evidence or weights.

The default discovery policy is `same maker + same registration date`. Search
policy is intentionally separate from evidence evaluation, so future recall
policies can change without redefining what an observation means.

## Calibration linkage

The v0.7A calibration found maker equality to be a broad filter, while
same-maker + same-date greatly narrows candidates. Near-RJ works are strongly
enriched for registration clustering, but that signal is not proof of a
relationship. No bonus ground truth or exact timestamp calibration exists, so
the application makes no probability or precision claims.

Candidates use only local current cache or the latest valid historical snapshot;
the research XLSX is not a production data source. No crawling, fuzzy maker
matching, arbitrary RJ scanning, or background discovery is performed.
## Manual review

Users can label a displayed candidate as related, not related, or unsure.
Reviews are stored separately from DLsite-confirmed relations and do not
alter candidate generation, scoring, or confirmation semantics. A versioned
snapshot of the evidence shown at review time is retained for future manual
label analysis.

v0.9 only reads manual review events to build a descriptive evaluation dataset;
it does not change this candidate policy or promote candidates to confirmed
relations.
