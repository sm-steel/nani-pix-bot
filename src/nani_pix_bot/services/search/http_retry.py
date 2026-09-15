"""Shared rate-limit-retry logic for this package's four search
services — the two REST ones (jikan.py, tmdb.py) reach it through
rest.py's `get_json`, the two GraphQL ones (anilist.py, shikimori.py)
call it directly from graphql.py's `request`. They started out with a
near-identical 429/Retry-After retry loop each; this factors it out to
one place so there's a single implementation to get right."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus

import httpx
from loguru import logger

MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RETRY_AFTER_SECONDS = 5.0
# Matches the httpx client's own per-request timeout this package already
# uses elsewhere (see tmdb.py's `screenshots` docstring) — an upstream
# asking for a wait longer than the client would tolerate for a single
# request anyway isn't one this bot's interactive callback flow (a starter
# staring at a keyboard) should honor to the letter. Without a bound, a
# `Retry-After: 86400` response combined with MAX_RATE_LIMIT_RETRIES could
# park a handler for hours.
#
# That bound is per sleep, not per request: `request_with_retry` can hit
# this path up to MAX_RATE_LIMIT_RETRIES times for a single logical
# request, so the aggregate worst case is
# MAX_RATE_LIMIT_RETRIES * MAX_RETRY_AFTER_SECONDS = 150s of sleeping
# alone, before adding the httpx per-request timeout each of those retries
# can also spend. Raising MAX_RATE_LIMIT_RETRIES multiplies this bound
# directly — factor that in before doing so.
MAX_RETRY_AFTER_SECONDS = 30.0


def _parse_retry_after(raw_value: str | None, *, service_name: str) -> float:
    """Parses a `Retry-After` header value into a bounded, non-negative
    delay in seconds.

    RFC 9110 permits the header to be either delta-seconds (a plain
    number) or an HTTP-date; this tries delta-seconds first, then falls
    back to parsing an HTTP-date via the stdlib's own parser
    (`email.utils.parsedate_to_datetime`) and measuring the delta from
    now — Cloudflare (fronting AniList, see anilist.py's module comment)
    commonly sends the HTTP-date form. A value that is neither, that
    resolves to `nan`/`inf`/`-inf` (`float()` parses those strings without
    raising), or that resolves to a negative or absurdly large delay,
    falls back to `DEFAULT_RETRY_AFTER_SECONDS` / gets clamped to
    `MAX_RETRY_AFTER_SECONDS` rather than trusted outright — honoring a
    malformed or hostile value literally is exactly the "crash" or "park
    a handler indefinitely" failure modes this function exists to
    prevent, and a bad header is a recoverable anomaly, not our own bug,
    so it's logged as a WARNING rather than an ERROR."""
    if raw_value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        delay = float(raw_value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw_value)
        except ValueError:
            logger.warning(
                "{} sent an unparseable Retry-After header ({!r}), using the default {}s delay",
                service_name,
                raw_value,
                DEFAULT_RETRY_AFTER_SECONDS,
            )
            return DEFAULT_RETRY_AFTER_SECONDS
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        delay = (retry_at - datetime.now(UTC)).total_seconds()
    # `float()` accepts "nan"/"inf"/"-inf" without raising, so a header
    # like `Retry-After: nan` reaches here as a non-finite `delay` rather
    # than the ValueError branch above. `max(0.0, min(delay, ...))` happens
    # to resolve `nan` to `0.0` today, but only because of how CPython's
    # min/max order NaN comparisons — an accident of implementation, not a
    # documented guarantee, and `asyncio.sleep(nan)` hangs forever if a
    # future refactor of this expression stops relying on it. Treat
    # non-finite the same as any other value this function can't honor
    # literally: fall back to the default rather than trust the clamp to
    # keep saving it.
    if not math.isfinite(delay):
        logger.warning(
            "{} sent a Retry-After header ({!r}) resolving to a non-finite delay ({}), "
            "using the default {}s delay",
            service_name,
            raw_value,
            delay,
            DEFAULT_RETRY_AFTER_SECONDS,
        )
        return DEFAULT_RETRY_AFTER_SECONDS
    bounded_delay = max(0.0, min(delay, MAX_RETRY_AFTER_SECONDS))
    if bounded_delay != delay:
        logger.warning(
            "{} sent a Retry-After header ({!r}) resolving to {}s, clamping to [0, {}]",
            service_name,
            raw_value,
            delay,
            MAX_RETRY_AFTER_SECONDS,
        )
    return bounded_delay


async def request_with_retry(
    make_request: Callable[[], Awaitable[httpx.Response]], *, service_name: str, context: str
) -> httpx.Response:
    """Calls `make_request()` up to `MAX_RATE_LIMIT_RETRIES` times, sleeping
    and retrying on a 429 (honoring the response's `Retry-After` header, or
    `DEFAULT_RETRY_AFTER_SECONDS` if absent — see `_parse_retry_after` for
    how a malformed or out-of-bounds header value is handled). Any other
    error status raises via `response.raise_for_status()`; exhausting all
    retries raises RuntimeError. `service_name`/`context` are only used for
    log/error messages — callers pass whatever identifies the request being
    made."""
    for _attempt in range(MAX_RATE_LIMIT_RETRIES):
        response = await make_request()
        if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
            response.raise_for_status()
            return response
        retry_after = _parse_retry_after(
            response.headers.get("Retry-After"), service_name=service_name
        )
        logger.warning("{} rate-limited request, retrying in {}s", service_name, retry_after)
        await asyncio.sleep(retry_after)
    msg = f"{service_name} rate limit retries exhausted ({context})"
    logger.error(msg)
    raise RuntimeError(msg)
