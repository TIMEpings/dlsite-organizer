# Fixture provenance

## Real regression fixtures

`dlsite/product_info_RJ01609020.json`,
`dlsite/product_info_RJ01636949.json`, and
`dlsite/product_info_RJ01637033.json` are reduced from user-captured, real
DLsite `product/info/ajax` responses. They retain the relevant real envelope,
field names, nesting, values, and null/array/object types. They contain no
cookie, session, or account identifiers.

`dlsite/product_metadata_RJ01609020.json`,
`dlsite/product_metadata_RJ01636949.json`, and
`dlsite/product_metadata_RJ01637033.json` are minimized, title-redacted
captures of the live `/{section}/api/=/product.json?workno=<RJ>&locale=ja_jp`
shape observed on 2026-08-31. They retain only the fields used by the rich
metadata contract: maker identity, age, registration date, voice, genres,
language edition, and primary image. The title value is intentionally
redacted; these files are endpoint-shape regression evidence, not a title
catalogue.

## Synthetic edge-case fixture

`product_semantic.html` is a synthetic Schema.org Product JSON-LD fixture.
It is only used to exercise the HTML fallback; it is not evidence of a live
DLsite response. Malformed AJAX inputs are constructed inline in tests and are
explicitly edge cases, not normal-response contracts.
Rich-source cases with a non-empty series, unknown language/age, or malformed
optional fields are also constructed inline and are explicitly synthetic.

`product_info_BJ00000001.json`, `product_metadata_BJ00000001.json`,
`product_info_VJ00000001.json`, and `product_metadata_VJ00000001.json` are
small neutral synthetic fixtures for the BJ/VJ routing and merge tests. They
contain no real work title or adult-content catalogue data; live BJ/VJ
observations are recorded without titles in `docs/dlsite-data-contract.md`.
