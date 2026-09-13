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

A decorator, not a function each caller invokes by hand: every
provider's search()/get_by_id()/screenshots() needs the exact same
caching shape, so hand-writing `cache.cached(client, "provider.search",
..., fetch=lambda: _search(...))` at each call site meant a hand-typed
cache-key string plus a private `_search`-shaped twin of every public
function that existed purely to be wrapped — duplication for the sake
of decoration. `@cache.cached()` collapses each pair back into one
function, the same way `functools.lru_cache` would if it supported
async functions and TTL expiry (it does neither, hence hand-rolling
this rather than reaching for a stdlib decorator).

Each decorated function gets its own private cache dict (a closure
variable, one per `@cached()` application) rather than every function
sharing one `dict[..., object]` — that would need every read to unbox
via `cast`/`# type: ignore`, since one shared dict can't statically
know which of many decorated functions' return types a given entry
holds. A private dict per function needs no such cast: its value type
is exactly that function's own `_T`, known at decoration time, so
`entry.get(key)` is already correctly typed on the way back out. The
only cost is `clear()` (test-only) reaching every private dict via a
list of their own `.clear` bound methods, rather than clearing one
dict directly — `Callable[[], None]` doesn't depend on `_T`, so that
list itself needs no cast either.

Keyed by the calling `httpx.AsyncClient`'s identity, not just the other
arguments: in production there's exactly one long-lived client per
provider family (`app.py`'s `search_client`/`tmdb_client`), so this
just means "cached for as long as that client is alive" — and in tests,
where every test constructs its own fresh client, this gives natural
per-test isolation for free, without needing a shared clear-the-cache
fixture (see test_cache.py)."""

import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

import httpx

DEFAULT_TTL_SECONDS = 300.0  # 5 minutes — long enough to dedupe rapid
# re-taps within one setup session, short enough that nothing goes
# meaningfully stale.

_T = TypeVar("_T")

# One bound `.clear` method per `@cached()`-decorated function, so the
# test-only clear() below can wipe every private cache without needing
# a single shared dict (see module docstring for why each function
# gets its own instead).
_registered_clears: list[Callable[[], None]] = []


def cached(
    ttl: float = DEFAULT_TTL_SECONDS,
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Decorates an async function whose first parameter is the
    `httpx.AsyncClient` making the call. The cache key is built
    automatically from that client's identity plus every other
    positional/keyword argument — nothing to hand-write per call site.
    Returns the cached value if it was stored less than `ttl` seconds
    ago, else calls the wrapped function, caches the result, and
    returns it."""

    def decorator(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        # Private to this one decorated function — see module docstring
        # for why that's what makes every read below fully typed as
        # `_T`, with no cast needed to unbox it.
        own_cache: dict[tuple[object, ...], tuple[float, _T]] = {}
        _registered_clears.append(own_cache.clear)

        @wraps(func)
        async def wrapper(client: httpx.AsyncClient, *args: object, **kwargs: object) -> _T:
            key = (id(client), args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            entry = own_cache.get(key)
            if entry is not None:
                cached_at, value = entry
                if now - cached_at < ttl:
                    return value

            value = await func(client, *args, **kwargs)
            own_cache[key] = (now, value)
            return value

        return wrapper

    return decorator


def clear() -> None:
    """Test-only escape hatch — see test_cache.py's autouse fixture."""
    for clear_one in _registered_clears:
        clear_one()
