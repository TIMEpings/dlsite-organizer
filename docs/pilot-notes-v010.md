# v0.10 Candidate Review Pilot Notes

These notes summarize product-level findings from the first ten manually
reviewed candidate pairs. They are not a random accuracy evaluation and do
not contain private reviewer notes.

- Full titles are required for reliable pair review; table ellipsis is not
  sufficient when titles share a long prefix.
- Covers are frequently useful evidence for human review when locally
  available.
- Same-maker/same-date groups still require pair-level review.
- RJ numeric proximity did not correspond monotonically to the eventual
  relation label.
- The pilot exposed taxonomy gaps around same-series pairs, official language
  sibling variants, and directional bundle containment. These gaps are
  recorded here only; no new relation types are introduced in v0.10.1.

The queue remains a derived local view using policy `same-maker-same-date`
version `1`. It has no score, probability, ranking model, auto-confirmation,
or auto-learning behavior.
