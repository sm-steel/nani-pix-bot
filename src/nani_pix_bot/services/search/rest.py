"""Shared JSON-over-HTTP plumbing for the three REST providers —
shikimori.py, jikan.py and tmdb.py. (anilist.py is GraphQL: one POST to
one endpoint, no by-id URL and no 404 semantics, so it shares nothing
here and shouldn't be forced to.)

Each of those three had its own byte-identical `_request` and its own
copy of the same by-id-with-404-handling dance, differing only in a
log label, a URL and which parse function to call. `qlty smells`
flagged the duplication — intermittently, since it sat right on the
mass threshold, which made it a coin-flip failure of the pre-commit
hook and CI (issue #68).

What deliberately stays per-provider is the *parsing*: each module
keeps its own `_parse_*` and its own concretely-typed public
`get_by_id`. `fetch_by_id` is generic over the parsed type, so
`ShikimoriResult | None` stays `ShikimoriResult | None` to `ty` rather
than collapsing into a union of all three that callers would have to
narrow back down by hand — the same constraint that keeps
`screenshot_gallery.py::_screenshot_search_step` dispatching on an
if/elif chain instead of a provider->function dict."""

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, TypeVar

import httpx
from loguru import logger

from nani_pix_bot.services.search import http_retry

_T = TypeVar("_T")


@dataclass(frozen=True)
class RestApi:
    """One provider's identity for the helpers below: the name that
    appears in its log lines and rate-limit retry messages, plus any
    headers it wants on every request.

    `headers` is None for TMDB, whose Bearer token is set as a default
    on the client itself in app.py rather than per request."""

    name: str
    headers: dict[str, str] | None = None


async def get_json(api: RestApi, client: httpx.AsyncClient, url: str, params: dict) -> Any:
    """GET `url` and decode the body. Returns `Any` rather than `dict`
    because providers disagree on the shape: Shikimori's list endpoints
    answer with a bare JSON array while Jikan and TMDB wrap everything
    in an object. Callers know their own provider's shape and index
    into it directly.

    Rate limits (429) are retried inside `http_retry.request_with_retry`;
    every other error status raises.

    A 200 whose body isn't JSON at all — a throttle page served as HTML,
    a proxy error page, a truncated response — becomes a `RuntimeError`
    rather than the `ValueError` `response.json()` would raise on its
    own: `RuntimeError` is in the handlers' `_SEARCH_SERVICE_ERRORS`
    tuple, so the starter gets the "service is down" reply instead of
    being stranded on a SETUP row with a dead keyboard (issue #75)."""

    async def make_request() -> httpx.Response:
        return await client.get(url, params=params, headers=api.headers)

    response = await http_retry.request_with_retry(
        make_request, service_name=api.name, context=f"url {url!r}, params {params!r}"
    )
    try:
        return response.json()
    except ValueError as exc:
        msg = f"{api.name} returned a non-JSON body for {url!r}"
        logger.error(msg)
        raise RuntimeError(msg) from exc


async def fetch_by_id(
    api: RestApi, client: httpx.AsyncClient, url: str, entity_id: int, parse: Callable[[Any], _T]
) -> _T | None:
    """Re-fetch a single entity by id, returning None if the provider no
    longer has it.

    A 404 is an expected outcome here, not a failure: the starter tapped
    a search result that has since been removed, and the picker tells
    them so (`dm_start.not_found_anymore`). Anything else — including a
    provider being down — propagates, so it reaches the caller's
    `_SEARCH_SERVICE_ERRORS` handling and is reported as an outage
    rather than silently looking like a missing entry."""
    try:
        raw = await get_json(api, client, url, {})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("{} id {} no longer found", api.name, entity_id)
            return None
        raise
    return parse(raw)
