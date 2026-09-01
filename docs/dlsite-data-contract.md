# DLsite metadata source contract (Phase A + WorkCode compatibility)

This document records the Phase A metadata-correctness recovery audit on
2026-08-31. It separates verified live source behavior from the older
synthetic HTML fallback fixture and from the public reference implementation
studied during the audit. The additional RJ/BJ/VJ routing audit was performed
with small read-only probes on 2026-09-01; no response body or adult title was
written to the repository.

## WorkCode contract and bounded routing

`WorkCode` is the single parser and domain identity for all supported DLsite
work numbers. It accepts case-insensitive `RJ`, `BJ`, and `VJ` prefixes, plus
six through ten decimal digits, and stores the normalized uppercase form. The
six-to-ten range is the existing application contract retained for historical
and future identifiers; it is not a claim that every current identifier is
exactly eight digits. The live samples included eight-digit `RJ`/`BJ` numbers
and both six- and eight-digit `VJ` numbers.

Folder extraction uses the same parser. A basename with zero valid codes is
not a candidate, one distinct normalized code is valid, repeated occurrences
of that code are deduplicated, and two or more distinct codes are ambiguous
and fail closed. The same normalized `Work.workno` is used by Lookup,
Organizer, Quick Rename, Naming, persistence, and cache lookup. `{workno}`
renders all three prefixes; the legacy `{rjcode}` formatter alias resolves to
that same current work number for compatibility and is not advertised in the
UI.

Routing is centralized in `DlsiteSection`/`source_route_for`:

| WorkCode prefix | Public page route | Structured/rich route | Routing rule |
| --- | --- | --- | --- |
| RJ | `/{configured-section}/work/=/product_id/<WORKNO>.html` | same configured section | preserve the existing configured section |
| BJ | `/books/work/=/product_id/<WORKNO>.html` | `/books/product/info/ajax`, `/books/api/=/product.json` | deterministic `books` section |
| VJ | `/soft/...` or `/pro/...` | resolved final section | request `soft`, follow one public-page redirect, then use the final `soft` or `pro` section; bounded fallback is `pro` |

The VJ policy has exactly two known sections and does not brute-force the
storefront. HTML fallback uses the same resolved section. Prefixes do not
select different domain or naming pipelines; only the provider route seam is
prefix-aware.

## Prefix capability matrix

The following matrix records the observed capability of the current public
routes. `YES` means the field/source was present in the reviewed sample;
`OPTIONAL` means the endpoint is valid but the category or listing may omit
the field; `NO` means the reviewed source does not provide that field; and
`UNVERIFIED` means this small probe did not establish a reliable contract.
The source columns distinguish core AJAX from rich product JSON where that
matters.

| Prefix | Valid public page | AJAX | Rich JSON | Required section | title | maker_id | maker_name | series | CV | tags | age | language | release date | cover | translation_info | bonuses |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RJ | YES: `maniax` sample | YES | YES | `maniax` in probe; configured for app | YES / YES | YES / YES | OPTIONAL / YES | OPTIONAL | YES | YES | YES / YES | OPTIONAL / OPTIONAL | YES / YES | YES / YES | YES | YES |
| BJ | YES: `books` sample | YES | YES | `books` | YES / YES | YES / YES | OPTIONAL / YES | OPTIONAL | OPTIONAL | YES | YES / YES | OPTIONAL | YES / YES | YES / YES | YES | YES |
| VJ | YES: `soft` and `pro` samples | YES after route resolution | YES after route resolution | resolved `soft` or `pro` | YES / YES | YES / YES | OPTIONAL / YES | OPTIONAL | YES | YES | YES / YES | OPTIONAL | YES / YES | YES / YES | YES | YES |

For the `title` through `cover` columns, a pair such as `YES / OPTIONAL`
means core AJAX / rich JSON respectively. `translation_info` and `bonuses`
are core AJAX fields; the rich endpoint has `NO` for those provider-local
fields. Rich `series`, CV, tags, language, and cover remain optional at the
normalized `Work` boundary even when the endpoint supports their shape.

The live samples used for this matrix were `RJ01609020`, `BJ01755969`,
`VJ01002044`, and `VJ009933`. Their public responses were not persisted. The
semantic validation report is intentionally title-free:

| WorkNo | resolved section | core | rich | maker id | series | CV count | tag count | age | language |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |
| `RJ01609020` | `maniax` | 200 | 200 | `RG01058997` | absent | 1 | 6 | 1 / GENERAL | `JPN` |
| `BJ01755969` | `books` | 200 | 200 | `BG01210` | present | 0 | 20 | 3 / R18 | absent |
| `VJ01002044` | `soft` after public redirect | 200 | 200 | `VG01000142` | absent | 8 | 6 | 1 / GENERAL | absent |
| `VJ009933` | `pro` after public redirect | 200 | 200 | `VG01723` | absent | 9 | 5 | 3 / R18 | absent |

All four probes also returned an HTTP 200 product page with JSON-LD, but the
observed JSON-LD was not a stable `Product` payload for the normalized rich
fields. The HTML parser therefore remains a fail-soft fallback, not a claim
that HTML provides the matrix fields. A missing rich field is represented as
`None` or an empty tuple/list and the lookup still succeeds.

## Source matrix

The original source audit used the three reviewed translation-topology works
(`RJ01609020`, `RJ01636949`, and `RJ01637033`) in the configured `maniax`
section and repeated the endpoint shape against `home`. The compatibility
audit above adds one or two small samples for each newly supported prefix.
Responses were not written to the repository.

| Field / evidence | `product/info/ajax` | `api/=/product.json` | Product-page HTML |
| --- | --- | --- | --- |
| title | YES: `work_name` | YES: `work_name` | UNVERIFIED in the live parser contract |
| listing maker id/name | ID YES; name optional | YES: `maker_id`, `maker_name`, `circle_id` | UNVERIFIED |
| series | NO in reviewed AJAX | YES-shaped: `series_id`, `series_name` (null in reviewed samples) | UNVERIFIED |
| CV | NO in reviewed AJAX | YES: `creaters.voice_by[].name` | UNVERIFIED |
| tags | NO in reviewed AJAX | YES: `genres[].name` | UNVERIFIED |
| work language | Translation `lang` only when explicit | YES: exact `language_editions[]` match when listed | UNVERIFIED |
| age | YES: numeric `age_category` | YES: `age_category` / `age_category_string` | NO modeled field |
| release/registration | YES: `regist_date` | YES: `regist_date` | UNVERIFIED |
| cover | YES: `work_image` | YES: `image_main.url` | UNVERIFIED |
| translation topology | YES: `translation_info` | NO modeled field | NO modeled field |
| bonus evidence | YES: `bonuses` | NO modeled field | NO modeled field |

### Live endpoint observations

The core endpoint
`https://www.dlsite.com/<section>/product/info/ajax?product_id=<WORKNO>`
returned HTTP 200 JSON objects keyed by the requested RJ, BJ, or VJ work
number for the routes in the matrix. The rich endpoint
`https://www.dlsite.com/<section>/api/=/product.json?workno=<WORKNO>&locale=ja_jp`
returned HTTP 200 JSON arrays containing one product object. BJ required the
`books` section in the live probe. VJ public-page redirects resolved to
`soft` or `pro`, and the provider then used that resolved section for both
structured endpoints.

The live product pages returned HTTP 200 HTML and contained JSON-LD documents,
but the inspected documents were `BreadcrumbList`/`WebSite`, not a usable
Schema.org `Product`. The child HTML request also redirected to the parent
page with a `translation=<child>` query. The existing `HtmlProductSource`
parser therefore remains a deliberately narrow fallback tested by the
synthetic `tests/fixtures/product_semantic.html`; it is not presented as a
current live rich-metadata source.

The minimized rich fixtures in `tests/fixtures/dlsite/` retain the verified
product-JSON field shape and values while redacting titles. The three AJAX
fixtures retain the reviewed translation and bonus envelope needed for
relation and evidence regression tests.

The product JSON language-edition list is an edition catalogue, not a promise
that every translated RJcode is repeated in the response: the live child
response listed the original and sibling edition codes but not
`RJ01637033`. The child therefore obtains `CHI_HANS` from its explicit core
`translation_info.lang` after the exact-edition lookup has no match.

## Reviewed translation topology

The AJAX samples preserve this explicit graph:

```text
RJ01609020 (is_original)
  └─ RJ01636949 (is_parent; original_workno = RJ01609020)
       └─ RJ01637033 (is_child; original_workno = RJ01609020,
                        parent_workno = RJ01636949)
```

`TranslationInfoSource` remains provider-local. `TranslationRelationService`
still owns role interpretation and edge creation; rich metadata enrichment
does not request or infer additional relation nodes.

## Typed source and merge policy

Raw JSON is stopped in `providers/dlsite/sources.py`. The product JSON array
is validated into `ProductMetadataSource` and its nested typed DTOs. No
`dict[str, Any]` product payload crosses into services or the UI.

`DlsiteProvider` uses a bounded flow:

1. Request and validate core `product/info/ajax`.
2. If core is valid, request rich product JSON for the exact listing.
3. If the listing is a translation child, request at most one rich product
   JSON object for `translation_info.original_workno`; child lists are never
   crawled recursively.
4. If core is unusable, make the existing single HTML fallback request.

The normalized `Work` contract now carries the listing identity and rich
fields: `maker_id`, `maker_name`, `series_name`, `cvs`/`cv_names`,
`tags`/`tag_names`, `language`, `age_category`, `release_date`,
`regist_datetime`, and `cover_url`.

The precedence rules are:

- Core AJAX is authoritative for title, translation topology, bonus evidence,
  availability, precise registration timestamp, and its cover when present.
- Rich product JSON supplies the descriptive maker pair, series, CVs, tags,
  exact listing language, age, and a cover/date fallback.
- A normal listing uses the rich maker ID/name pair when present, otherwise
  the core pair. A child listing never promotes its own translation publisher
  into `Work.maker_*`; it uses the original rich maker pair when available.
  The child publisher is retained as `TranslationAttribution`.
- A child original series is used when the original rich response carries a
  non-empty value; otherwise the requested listing's rich series is used.
  Rich CV/tag collections fall back to the core-normalized
  collection when the rich source did not capture them.
- Language is taken from the `language_editions` entry whose work number is
  exactly the requested listing. If that is unavailable, an explicit
  translation `lang` may identify a child listing. `locale=ja_jp` is only the
  metadata response locale; it never means that the work language is
  Japanese.
- Known age values are normalized as `1 → GENERAL`, `2 → R15`, and
  `3 → R18`. Unknown or malformed values become `UNKNOWN`; `work_type` is
  never used as an age signal.

If rich product JSON is unavailable, malformed, or returns a transient HTTP
error, the validated core `Work` is returned with missing rich fields. The
provider does not turn an optional rich failure into a false not-found result.
When both sources succeed, `LookupResult.source` is
`DLSITE_PRODUCT_INFO_AJAX+DLSITE_PRODUCT_JSON`, with `core_source` and
`metadata_source` retained separately. HTML fallback is labeled
`DLSITE_HTML_JSONLD`.

## Maker and translation attribution

`Work.maker_id` and `Work.maker_name` mean only the current DLsite listing's
maker/circle/publisher identity. They never mean translator, translation
publisher, or language attribution. A child lookup can therefore display:

```text
Work maker:          RG01058997 / original maker
Translation signoff: RG01001331 / MYHONYAKU
```

The UI places that signoff in the `作品关系` area and keeps the `社团` and
`社团编号` fields reserved for the resolved listing maker. Missing values
render as `—`; unknown language and age are explicit safe labels rather than
exceptions.

## Cache and history compatibility

`work_metadata_cache` gains nullable additive columns for normalized age,
language, translation attribution, and source provenance. A separate
nullable work-registration column preserves the distinction between the
existing cached source timestamp and a `Work` that actually carried its
registration timestamp. `metadata_observations` gains nullable series,
CV/tag JSON, language, attribution, and provenance columns.

Old cache rows load new fields as `None`/`UNKNOWN`. Old observation rows load
new fields as not captured; a NULL historical CV/tag JSON value is not
interpreted as an observed empty collection. Existing bonus evidence behavior
is unchanged:

```text
bonus_evidence_json = NULL  → legacy / not captured / unknown
entries = []                → explicitly observed empty bonuses
entries = [...]             → explicitly observed bonus evidence
```

SQLite upgrades use the repository's additive `ALTER TABLE ... ADD COLUMN`
strategy and do not delete or rewrite existing metadata, relation, review, or
rename-journal rows.

The cache primary key remains the normalized `workno` string; no prefix column
or migration is necessary. Because the complete canonical string is the key,
`RJ00000001`, `BJ00000001`, and `VJ00000001` are distinct identities.

## Naming, Lookup, and Organizer consumers

`NamingService` uses the fresh-profile default template
`[{workno}][{maker_name}]{title}` and can access the
correct normalized fields through `workno`/`rjcode`, `title`/`work_name`,
maker fields, `series_name`, `cv`/`cv_list`, `tags`/`tags_list`,
`age_category`, and `language`. This is only field access; settings and
full template customization remain outside Phase A.

`LookupService` persists the enriched `Work` and all provenance on a live
lookup, then returns the same fields on fresh/stale cache delivery. The
composition root gives `OrganizerService` that same `LookupService`, so an
Organizer preview receives the same enriched `Work` and naming result rather
than a second reduced metadata path.

## Reference implementation boundary

The public repository
[`yodhcn/dlsite-doujin-renamer`](https://github.com/yodhcn/dlsite-doujin-renamer)
was used only to cross-check public endpoint shape and field intent. Its
implementation is GPL; this MIT project independently models the verified
contract and does not copy its code.

## Representative acceptance evidence

The following table summarizes the live values used to validate the rich
normalization path; titles are intentionally omitted.

| Work | resolved maker | series | CV count | tag count | age | language |
| --- | --- | --- | ---: | ---: | --- | --- |
| `RJ01609020` | `RG01058997 / のりプロ` | absent | 1 | 6 | GENERAL | JPN |
| `RJ01636949` | `RG60289 / みんなで翻訳` | absent | 1 | 6 | GENERAL | CHI_HANS |
| `RJ01637033` | original maker `RG01058997 / のりプロ` | absent | 1 | 6 | GENERAL | CHI_HANS |
| synthetic neutral rich case | `RG-SYNTH / Synthetic Circle` | Synthetic Series | 1 | 1 | R15 | ENG |

For the child row, `RG01001331 / MYHONYAKU` is retained as translation
attribution, not as the resolved `Work.maker_*`. The live samples happened to
have no series value and no reliable live R15 sample was available during this
minimal probe; the synthetic neutral row and contract tests cover non-empty
series, R15, R18, unknown age, unknown language, and malformed optional rich
fields.
