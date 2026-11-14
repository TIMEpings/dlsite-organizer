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

## Limited bonus search

Lookup offers a user-initiated RJ search. The local candidate universe consists
of same-maker work summaries within the configured registration-date tolerance
(0 to 30 days, default 7). Individually probed local seeds use the same-day index.
Listings admitted by a date difference are pending manual review. The exact AJAX endpoint can verify an
RJ supplied by the user or referenced by the main work's bonus entry, even
when it is absent from the local index. Each search allows one main lookup
and at most five priority candidate lookups. Every range checks local relation
evidence and cached listings first. When there are saved candidates awaiting
review, ordinary lookup and “查看待确认候选” use local data. Changing the range
does not start a search. “继续在线查找更多特典” explicitly searches the selected
range; the exact-RJ verification action remains available. In maniax,
the remaining RJ codes can be queried in batches. The default is +1 through
+100. Custom ranges accept signed offsets and exclude offset zero. Each search may issue up to 20 batches
of at most five codes, one at a time with a ten-second interval between
request starts. After four minutes no new range-search requests are started.
HTTP errors, connection failures and malformed batch entries stop the online
search. Valid results from earlier requests and the current batch are retained.

In the November 2026 probe, the independent bonus IDs `RJ01690654` and
`RJ01656587` both returned AJAX metadata, with a wishlist count of 1.
Their public pages returned 404 with no redirect, and exact-RJ storefront
search did not list either work. The feature uses the no-sale, free,
`is_oly` and zero-price fields for candidate screening. Wishlist count
is retained as observed metadata.

On 2026-11-13, two maniax requests each combined the active pair
`RJ01741384` / `RJ01741449`, the historical pair `RJ01468440` /
`RJ01468442`, and an invalid ID. The five-ID probe returned the four valid
envelopes in HTTP 200 JSON (19,445 bytes), omitting the invalid ID. The
active pair shared the exact campaign title, maker and registration date.
The historical bonus matched the bonus status profile, maker and date.
This probe established behavior for that request size and route. The
configured limits cap production load. Batch limits at the storefront,
other sections, and a live +100 scan remain unverified.

Progress counts valid screened listings, omitted IDs, malformed entries
and unsubmitted IDs separately. Omissions apply to the response and are
queried again on a later search. Fresh listing observations count towards
range coverage. Older observations support historical matching and can be
rechecked online. Search coverage cannot establish that a bonus never existed.

The historical coverage reference uses an offline aggregate of 494 valid RJ
pairs observed in 2026: 493 positive differences, one difference of -18,545,
and no zero difference. Positive cumulative nodes extend through the observed
maximum of +172,074, where the sample reaches 493/494 (about 99.8%). Larger
positive endpoints stay at that sample plateau without extrapolation. A range
containing both -18,545 and +172,074 covers all 494 pairs in this sample. This
is a sample-only reference, not DLsite's actual bonus discovery rate or a
guarantee; purely negative ranges and partial negative coverage have no
reliable estimate.

New AJAX observations retain a nullable `bonus_listing_json` snapshot
with the listing title, maker, registration date and bonus status fields.
The `limited_bonus_relations` table stores one evidence record per directional
main/bonus pair. An active campaign with a known deadline can establish a
high-confidence automatic association during a search. It requires fresh
DLsite AJAX observations, exact title, maker and registration-date matches,
an independent bonus listing, and no known competing evidence. The stored
source `ajax_title_cross_validation` identifies this result alongside
direct RJ evidence and pending candidates. Expiry affects eligibility for new
associations. Saved associations and historical campaign dates remain readable
from both works' lookup pages.

“更改判断” places an automatic association back in review. Confirming,
rejecting or restoring a pair to pending appends a decision to
`manual_relation_reviews` with the evidence visible at review time. The
directional decision takes precedence over automatic matching. Users can
explicitly search for more bonuses from the same main work.
Scan results can also seed the ordinary lookup cache without replacing entries
written by ordinary lookups. The setting for scan metadata is enabled by default.
Turning it off preserves existing reads, pair evidence, and manual reviews.
