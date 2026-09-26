# Historical bonus evidence

Version 0.10.3 retains the `bonuses` field reported by a successful DLsite
`product/info/ajax` response in the append-only `metadata_observations`
history. This is evidence preservation only; it does not detect bonuses,
classify works, or create a `BONUS_OF` relation.

The provider maps each entry into a typed `BonusEvidence` value and the
application stores a versioned `BonusEvidenceSnapshot`. The snapshot keeps
the source order and preserves the available title/name, description/body,
start and end date/time, bonus work number, product id, URL, type, and label.
Date-only values remain dates; timestamp values retain their time and
timezone information. Optional malformed values are treated as unavailable
and are not replaced with guessed values.

The nullable `metadata_observations.bonus_evidence_json` column has three
distinct meanings:

| Stored value | Meaning |
| --- | --- |
| `NULL` | Legacy observation, non-AJAX observation, or bonus field not captured/available |
| `{"schema_version":1,"entries":[]}` | Successful live response explicitly reported `bonuses: []` |
| `{"schema_version":1,"entries":[...]}` | Successful live response explicitly reported one or more bonus entries |

`MetadataStore.list_bonus_observations(workno)` is a local-only read path. It
does not call a provider. Existing historical translation evidence remains in
`translation_json`; adding the bonus column does not alter current cache TTL,
stale fallback, or historical translation relation reconstruction.

Bonus evidence stored in historical observations represents what DLsite
reported at observation time. Empty bonuses and unavailable legacy bonus
evidence are different states. Historical bonus evidence is not itself a
confirmed `BONUS_OF` relation.

## Longitudinal expiration pilot

**Sample:** main work `RJ01690645`; bonus work `RJ01690654`

**Observed bonus deadline:** `2026-09-06 23:59:59`

**Verdict:** PASS

The reported pre-expiry and post-expiry AJAX observations show this transition:

| Observation | Main work (`RJ01690645`) | Bonus work (`RJ01690654`) |
| --- | --- | --- |
| Before expiry | `bonuses` contains an explicit entry with a description, `dist_flg = "1"`, `end_date = "2026-09-06 23:59:59"`, and `end_date_str = "2026/09/06 23:59"`. | Queryable. |
| After expiry | `bonuses == []`; the current response no longer exposes that live entry. | Still queryable; maker, registration date, title, and free/on-sale identity fields remain materially unchanged. Mutable rating statistics changed. |

Conclusion: DLsite may remove explicit live bonus evidence from the main
work's AJAX response after expiry while leaving the bonus work available in
AJAX data. This is one real longitudinal positive sample. It is regression
evidence, not a universal or undocumented DLsite API guarantee.

This transition changes only the current observation. It does not erase an
earlier positive observation or establish that the work historically never
had a limited-time bonus. The persisted distinction remains:

```text
current explicit live evidence  ≠ historical positive evidence
explicit empty response         ≠ historical negative fact
NULL                            = unknown / legacy / not captured
```

Bonus title wording or morphology is **not** an identity rule. In particular,
title similarity, a shared title stem, or words such as `特典` and `早期購入`
are not required to discover or preserve a possible bonus relationship. The
sample's title pattern is sample-specific and must not be generalized.
