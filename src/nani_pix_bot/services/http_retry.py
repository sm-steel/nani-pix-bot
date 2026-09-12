"""Shared rate-limit-retry logic for services that hit external HTTP
APIs (services/anilist.py, services/shikimori.py) — both had a
near-identical 429/Retry-After retry loop; this factors it out to one
place so there's a single implementation to get right."""

import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus

import httpx
from loguru import logger

MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RETRY_AFTER_SECONDS = 5.0


async def request_with_retry(
    make_request: Callable[[], Awaitable[httpx.Response]], *, service_name: str, context: str
) -> httpx.Response:
    """Calls `make_request()` up to `MAX_RATE_LIMIT_RETRIES` times, sleeping
    and retrying on a 429 (honoring the response's `Retry-After` header, or
    `DEFAULT_RETRY_AFTER_SECONDS` if absent). Any other error status raises
    via `response.raise_for_status()`; exhausting all retries raises
    RuntimeError. `service_name`/`context` are only used for log/error
    messages — callers pass whatever identifies the request being made."""
    for _attempt in range(MAX_RATE_LIMIT_RETRIES):
        response = await make_request()
        if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
            response.raise_for_status()
            return response
        retry_after = float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS))
        logger.warning("{} rate-limited request, retrying in {}s", service_name, retry_after)
        await asyncio.sleep(retry_after)
    msg = f"{service_name} rate limit retries exhausted ({context})"
    logger.error(msg)
    raise RuntimeError(msg)
