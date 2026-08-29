"""Parse semantic metadata from a DLsite product page.

The parser consumes provider-local HTML rather than exposing page structure to
the domain. It currently trusts only Schema.org JSON-LD fields and degrades
optional fields when they are absent.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from dlsite_organizer.domain.work import Availability, Work
from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError


@dataclass(frozen=True, slots=True)
class HtmlProductSource:
    """Provider-local data extracted from Schema.org Product JSON-LD."""

    title: str
    maker_id: str | None
    maker_name: str | None
    release_date: date | None
    series_name: str | None
    cvs: list[str]
    tags: list[str]
    cover_url: str | None


def parse_product_page(html: str, workno: str, *, section: str) -> Work:
    """Legacy convenience wrapper for HTML fallback normalization.

    ``parse_product_page_source`` owns the HTML-specific extraction.  Keeping
    this wrapper preserves the v0.1 parser API for callers and tests.
    """
    return normalize_product_page_source(
        parse_product_page_source(html),
        workno=workno,
        section=section,
    )


def parse_product_page_source(html: str) -> HtmlProductSource:
    """Extract provider-local semantic data from HTML JSON-LD."""
    product = _find_product_document(html)
    title = _text(product.get("name"))
    if title is None:
        raise DlsiteParseError("Product metadata did not contain a title")

    maker_id, maker_name = _parse_maker(product)
    return HtmlProductSource(
        title=title,
        maker_id=maker_id,
        maker_name=maker_name,
        release_date=_parse_date(product.get("releaseDate") or product.get("datePublished")),
        series_name=_parse_series(product),
        cvs=_parse_people(product.get("actor")),
        tags=_parse_keywords(product.get("keywords")),
        cover_url=_parse_image(product.get("image")),
    )


def normalize_product_page_source(
    source: HtmlProductSource,
    *,
    workno: str,
    section: str,
) -> Work:
    """Normalize HTML semantic data without exposing its structure to domain code."""
    return Work(
        workno=workno,
        title=source.title,
        maker_id=source.maker_id,
        maker_name=source.maker_name,
        release_date=source.release_date,
        series_name=source.series_name,
        cvs=source.cvs,
        tags=source.tags,
        cover_url=source.cover_url,
        availability=Availability.AVAILABLE,
        source_section=section,
    )


def _find_product_document(html: str) -> Mapping[str, Any]:
    tree = HTMLParser(html)
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text(strip=True)
        if not raw:
            continue
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _iter_json_ld_items(document):
            item_type = item.get("@type")
            types = {item_type} if isinstance(item_type, str) else set(item_type or [])
            if "Product" in types:
                return item
    raise DlsiteParseError("Product page did not contain usable Product JSON-LD")


def _iter_json_ld_items(document: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(document, Mapping):
        graph = document.get("@graph")
        if isinstance(graph, list):
            yield from (item for item in graph if isinstance(item, Mapping))
        yield document
    elif isinstance(document, list):
        yield from (item for item in document if isinstance(item, Mapping))


def _parse_maker(product: Mapping[str, Any]) -> tuple[str | None, str | None]:
    candidate = product.get("brand") or product.get("manufacturer")
    if isinstance(candidate, str):
        return None, _text(candidate)
    if isinstance(candidate, Mapping):
        identifier = candidate.get("identifier")
        maker_id = _text(identifier) if isinstance(identifier, (str, int)) else None
        return maker_id, _text(candidate.get("name"))
    return None, None


def _parse_date(value: Any) -> date | None:
    text = _text(value)
    if text is None:
        return None
    normalized = text.strip().replace("/", "-")
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            return None


def _parse_series(product: Mapping[str, Any]) -> str | None:
    candidate = product.get("isPartOf")
    if isinstance(candidate, str):
        return _text(candidate)
    if isinstance(candidate, Mapping):
        return _text(candidate.get("name"))
    return None


def _parse_people(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else [value]
    names: list[str] = []
    for candidate in candidates:
        name = _text(candidate.get("name")) if isinstance(candidate, Mapping) else _text(candidate)
        if name:
            names.append(name)
    return names


def _parse_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [text for item in value if (text := _text(item)) is not None]
    return []


def _parse_image(value: Any) -> str | None:
    candidate: Any = value[0] if isinstance(value, list) and value else value
    if isinstance(candidate, Mapping):
        candidate = candidate.get("url") or candidate.get("contentUrl")
    image = _text(candidate)
    if image is None:
        return None
    return urljoin("https://www.dlsite.com", image)


def _text(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    stripped = str(value).strip()
    return stripped or None
