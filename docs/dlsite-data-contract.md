# DLsite AJAX data contract

This note records only facts observed in three reviewed, user-captured
`product/info/ajax` JSON responses. It does not make a live-network claim.

## Samples

| Requested work | Observed translation flags | Observed references |
| --- | --- | --- |
| `RJ01609020` | `is_original: true`, `is_parent: false`, `is_child: false` | No original or parent work number; empty child list |
| `RJ01636949` | `is_original: false`, `is_parent: true`, `is_child: false` | `original_workno: RJ01609020`; child list includes `RJ01637033`, `RJ01636950`, and `RJ01663275` |
| `RJ01637033` | `is_original: false`, `is_parent: false`, `is_child: true` | `original_workno: RJ01609020`; `parent_workno: RJ01636949` |

The samples support this structural topology:

```text
RJ01609020 (is_original)
  └─ RJ01636949 (is_parent; original_workno = RJ01609020)
       └─ RJ01637033 (is_child; original_workno = RJ01609020,
                        parent_workno = RJ01636949)
```

`RJ01636949` lists `RJ01637033` as a child. Its other listed child work
numbers are not independently sampled, so no further topology is claimed.
The response labels distinguish `original`, `parent`, and `child`; this code
does not equate any of those terms with a broader business meaning beyond the
observed flags and references.

## AJAX envelope and identity

Every sample is a JSON object with exactly one top-level key, equal to the
requested RJ code. The nested product object does **not** contain `workno` in
any sample. `RJ01637033` alone contains `product_id`, whose value equals its
envelope key; the other two omit `product_id`.

The parser therefore treats the requested/envelope key as the work identity
used for domain `Work.workno`. It retains `requested_workno`,
`envelope_workno`, and optional raw `product_id` separately in the provider
DTO, and rejects a present `product_id` that disagrees with the envelope. This
avoids treating an image path (which points at the `RJ01609020` image in all
three samples) as product identity.

## Core metadata

All samples contain `work_name`, `maker_id`, `regist_date`, `work_image`,
`work_type`, and numeric `age_category`.

`maker_name` is absent in `RJ01609020` and `RJ01636949`, and is the string
`MYHONYAKU` in `RJ01637033`. `maker_id` is separately present in all three:
`RG01058997`, `RG60289`, and `RG01001331`, respectively. The DTO keeps both
fields independent.

All observed `regist_date` values are naive timestamp strings in
`YYYY-MM-DD 00:00:00` form: `2026-05-26 00:00:00` for `RJ01609020` and
`2026-07-07 00:00:00` for the other two. The provider retains this as
`regist_datetime`. `Work.release_date` is its date component solely for the
current UI/domain shape; interpreting `regist_date` as a public-release,
registration-creation, or sales-start date is not justified by these samples.

## Translation source model

All samples have a `translation_info` object with boolean
`is_translation_agree`, `is_volunteer`, `is_original`, `is_parent`,
`is_child`, and `is_translation_bonus_child`, plus `original_workno`,
`parent_workno`, `child_worknos`, and `lang`. Null and empty-array values are
preserved in the reduced regression fixtures.

`is_translation_bonus_child` is retained without assigning it bonus semantics.
Other response fields in this object are not currently part of the typed DTO:
`production_trade_price_rate`, `translation_bonus_langs`, and
`translation_status_for_translator`. The latter is an object in `RJ01609020`
and an empty array in the other two samples, so it is not yet a stable source
contract.

`DlsiteProvider.fetch_work_lookup()` returns both normalized `Work` and the
validated `ProductInfoAjaxSource` in one request flow. The application exposes
only its nested `translation_info` to `TranslationRelationService`, which
maps explicit flags and references into confirmed directional relations
without re-requesting or reparsing the raw response. `fetch_work()` remains
the metadata-only API.

The current interpretation is intentionally narrow: a true role flag is
required to identify Original, Translation Parent, or Translation Child, and
each relation target must be a concrete work number from the response. An
absent or role-less `translation_info` means that no explicit relation
information was observed; it is not a proof that translations do not exist.

## Future-interest fields

Each sample has `bonuses: []`, `is_limit_work: false`, and
`sales_end_info: null`. These observed values and types are deliberately not
interpreted as a bonus or limited-sale mechanism. `RJ01636949` also has a
`translators` field; it is not modeled in this release.

The observed `bonuses` field is now retained when a successful AJAX lookup
contains it. The retention schema and its `NULL` versus explicit-empty
semantics are documented in [Historical bonus evidence](bonus-evidence-history.md).
The reviewed samples contain only empty arrays, so they do not establish the
shape or longitudinal behavior of non-empty bonus entries.

## Remaining uncertainty

This small set does not establish that the envelope is universal, whether a
translated request can ever be keyed differently, the semantics of
`regist_date`, or the business meaning of parent/original/child outside these
explicit source flags. Those need targeted live-response verification before
they are generalized.
