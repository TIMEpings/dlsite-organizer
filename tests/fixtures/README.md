# Fixture provenance

## Real regression fixtures

`dlsite/product_info_RJ01609020.json`,
`dlsite/product_info_RJ01636949.json`, and
`dlsite/product_info_RJ01637033.json` are reduced from user-captured, real
DLsite `product/info/ajax` responses. They retain the relevant real envelope,
field names, nesting, values, and null/array/object types. They contain no
cookie, session, or account identifiers.

## Synthetic edge-case fixture

`product_semantic.html` is a synthetic Schema.org Product JSON-LD fixture.
It is only used to exercise the HTML fallback; it is not evidence of a live
DLsite response. Malformed AJAX inputs are constructed inline in tests and are
explicitly edge cases, not normal-response contracts.
