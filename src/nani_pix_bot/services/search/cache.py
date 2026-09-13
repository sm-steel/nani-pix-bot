"""Shared in-memory TTL cache for external search/screenshot API
responses — read-only *performance* caching, not authoritative flow
state. Losing this on a restart just costs one extra API call to
whichever provider is asked next; this is nothing like this project's
hard-won "flow state must be DB-derived, never cached in memory"
principle (issue #11), which is about a completely different kind of
state (what step a starter is on).

Exists specifically to avoid re-hitting a provider's API (and risking
429s) when a starter repeats an action during setup — re-running the
same search, tapping "More screenshots" back and forth, etc.

Keyed by the calling `httpx.AsyncClient`'s identity, not just the
operation/args: in production there's exactly one long-lived client per
provider family (`app.py`'s `search_client`/`tmdb_client`), so this
just means "cached for as long as that client is alive" — and in tests,
where every test constructs its own fresh client, this gives natural
per-test isolation for free, without needing a shared clear-the-cache
fixture (see test_cache.py)."""

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

DEFAULT_TTL_SECONDS = 300.0  # 5 minutes — long enough to dedupe rapid
# re-taps within one setup session, short enough that nothing goes
# meaningfully stale.

_T = TypeVar("_T")

_cache: dict[tuple[object, ...], tuple[float, object]] = {}


async def cached(
    client: httpx.AsyncClient,
    *key_parts: object,
    fetch: Callable[[], Awaitable[_T]],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> _T:
    """Returns the cached value for `(client, *key_parts)` if it was
    stored less than `ttl` seconds ago, else calls `fetch()`, caches the
    result, and returns it."""
    key = (id(client), *key_parts)
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None:
        cached_at, value = entry
        if now - cached_at < ttl:
            return value  # ty: ignore[invalid-return-type]

    value = await fetch()
    _cache[key] = (now, value)
    return value


def clear() -> None:
    """Test-only escape hatch — production code never needs to clear
    this explicitly, entries just age out via their own ttl."""
    _cache.clear()
