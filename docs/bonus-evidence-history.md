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

## Research note

Working hypothesis from the available cases:

```text
active time-limited bonus → parent AJAX may expose bonus metadata
expired case → parent current AJAX may no longer expose it
               while the bonus listing may remain queryable
```

This is a working hypothesis, not a universal DLsite contract and not yet
longitudinally proven. The project does not claim that DLsite always removes
bonuses immediately after expiry.
