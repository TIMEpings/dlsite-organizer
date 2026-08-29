# Fixture provenance

- `product_info_ajax_contract.json` is a synthetic, minimal
  `product_info_ajax` parser-contract fixture. It was not captured from DLsite
  and must not be treated as evidence that current production responses expose
  these fields or this envelope.
- `product_semantic.html` is a synthetic Schema.org Product JSON-LD fixture,
  not a captured DLsite product page.

When a real response can be collected and reviewed, retain only its relevant
semantic subset, record its source type here, and add it as a separate
regression fixture rather than silently changing these contracts.
