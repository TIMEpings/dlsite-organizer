# v0.10 Candidate Review Pilot Notes

These notes summarize product-level findings from the first 24 manually
reviewed candidate pairs. They are NOT AN ACCURACY ESTIMATE and were NOT
RANDOMLY SAMPLED; they do not contain private reviewer notes.

Pilot outcome counts:

```text
RELATED:     10
NOT_RELATED: 14
UNSURE:       0
```

- Full titles are required for reliable pair review; table ellipsis is not
  sufficient when titles share a long prefix.
- Covers are frequently useful evidence for human review when locally
  available.
- Same-maker/same-date groups still require pair-level review.
- RJ numeric proximity did not correspond monotonically to the eventual
  relation label.
- The pilot exposed repeated taxonomy gaps around same-series pairs, same-work
  non-language variants, official language sibling variants, and directional
  collection containment. These observations support the v0.10.2 taxonomy
  refinement, but do not establish accuracy.

Among the 10 related pairs, the earlier coarse labels exposed these repeated
relationships:

```text
OTHER: 7
- same series: 1
- same work, different version: 3
- same work, different language: 3

BUNDLED_WITH: 3
- all observed cases were directional collection/package containment
```

The new labels make these observations expressible as `SAME_SERIES`,
`SAME_WORK_VARIANT`, `SAME_WORK_LANGUAGE_VARIANT`, and `INCLUDED_IN`.
Existing `OTHER` and `BUNDLED_WITH` history remains unchanged; no application
startup migration or automatic historical reclassification is performed.

The queue remains a derived local view using policy `same-maker-same-date`
version `1`. It has no score, probability, ranking model, auto-confirmation,
or auto-learning behavior.
