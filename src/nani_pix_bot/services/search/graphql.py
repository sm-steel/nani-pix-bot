"""Shared GraphQL-over-HTTP plumbing for this package's GraphQL
providers — anilist.py, and shikimori.py since it migrated onto
GraphQL (issue #104) to sidestep a REST-API data-shape bug that
GraphQL's schema can't produce. Mirrors rest.py's shape: a small
provider-identity dataclass plus a couple of thin functions layered
directly on http_retry.py — not a full GraphQL client, just the two
pieces every GraphQL provider here needs and would otherwise each
reimplement byte-identically, the same duplication rest.py's docstring
describes for its own two REST providers.

What stays per-provider: the queries themselves, the variables they're
built with, and all parsing — this module only gets a caller as far as
a validated `data` object."""

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from nani_pix_bot.services.search import http_retry


@dataclass(frozen=True)
class GraphQLApi:
    """One provider's identity for the helpers below: the name that
    appears in its log lines and rate-limit retry messages, the
    endpoint URL every query POSTs to, and any headers it wants on
    every request (AniList needs a browser-like Referer; see
    anilist.py)."""

    name: str
    url: str
    headers: dict[str, str] | None = None


async def request(
    api: GraphQLApi, client: httpx.AsyncClient, *, query: str, variables: dict
) -> dict:
    """POST `query`/`variables` to `api.url` and hand back the response's
    `data` object.

    A GraphQL endpoint can sit behind an interstitial (AniList's
    Cloudflare, notably) that answers with HTML on a 200 — a body that
    doesn't decode, and one that decodes to anything other than an
    object (`null` decodes perfectly well and would otherwise reach
    callers as an `AttributeError`), both become a `RuntimeError`,
    which callers keep in their `_SEARCH_SERVICE_ERRORS` tuple (issue
    #75).

    GraphQL reports failures in an `errors` array rather than in the
    status code, so those are never dropped silently: they're always
    logged at ERROR, and when nothing usable came back with them this
    raises too. Falling back to an empty object there would tell a
    caller's user a flat lie about a search that never ran. A null
    `data` with no `errors` alongside it is just a missing container
    key, and does yield an empty object.

    The `data` object itself gets the same treatment as the body
    around it, via `require_object` below — a `{"data": 5}` body's
    scalar must not reach a caller's first `.get()` unvalidated (issue
    #85)."""

    async def make_request() -> httpx.Response:
        return await client.post(
            api.url,
            json={"query": query, "variables": variables},
            headers=api.headers,
        )

    response = await http_retry.request_with_retry(
        make_request, service_name=api.name, context=f"variables {variables!r}"
    )
    try:
        body = response.json()
    except ValueError as exc:
        msg = f"{api.name} returned a non-JSON body for variables {variables!r}"
        logger.error(msg)
        raise RuntimeError(msg) from exc
    if not isinstance(body, dict):
        msg = (
            f"{api.name} answered 200 with a {type(body).__name__} body "
            f"for variables {variables!r}, expected an object"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    data = body.get("data")
    if errors := body.get("errors"):
        msg = f"{api.name} reported GraphQL error(s) for variables {variables!r}: {errors!r}"
        logger.error(msg)
        if not data:
            raise RuntimeError(msg)
    return require_object(api, data, label="data", variables=variables)


def require_object(api: GraphQLApi, value: Any, *, label: str, variables: dict) -> dict:
    """`value` as a JSON object, `{}` when it simply isn't there, and a
    `RuntimeError` when it is there but isn't one.

    A GraphQL body nests containers inside `data` (AniList's `Page`,
    for instance) that no by-id URL or 404 status ever covers, so
    `rest.get_json`'s `expect=` doesn't reach them — every such nested
    container a caller reads should be passed through here, the same
    way `data` itself is inside `request` above, so nothing downstream
    is left holding an unvalidated container.

    Absent and malformed stay two different answers, the same split
    `parsing.require_int` draws: a null container key is an empty
    result a caller's picker already renders as "nothing found", while
    a number where an object belongs is a provider that can't be read
    at all — `RuntimeError`, which is in `_SEARCH_SERVICE_ERRORS`,
    unlike the `AttributeError` an unguarded `.get` would raise."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = (
            f"{api.name} answered 200 with a {type(value).__name__} in {label!r} "
            f"for variables {variables!r}, expected an object"
        )
        logger.error(msg)
        raise RuntimeError(msg)
    return value
