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

Keyed by the calling `httpx.AsyncClient` itself — a
`weakref.WeakKeyDictionary` of per-client caches, not a flat dict keyed
by `id(client)`. In production there's exactly one long-lived client
per provider family (`app.py`'s `search_client`/`tmdb_client`), so this
just means "cached for as long as that client is alive" — and in tests,
where every test constructs its own fresh client, this gives natural
per-test isolation for free, without needing a shared clear-the-cache
fixture (see test_cache.py). The weak key is what makes that literally
true rather than aspirational: `id(client)` kept no reference to the
client, so a collected client's entries stayed in the dict forever
(nothing ever purges, entries are only overwritten by a same-key
re-call) and CPython readily recycles addresses — a later client
allocated where a dead one used to live would read the dead one's value
back. Near-impossible in production (two clients, never replaced),
but exactly the shape of a rare, unreproducible test failure. Keying on
the object drops each client's entries the moment it's collected.

Two couplings worth knowing about before changing anything here:

- **A hit hands back the same object the first call returned**, not a
  copy — every caller of a cached `search()`/`screenshots()` shares one
  mutable list. Nothing mutates one today (the gallery only slices and
  indexes), and nothing should start: mutating a hit corrupts the entry
  for everyone who reads it afterwards.
- **The TTL is load-bearing for gallery paging**, not just a politeness
  towards providers. The screenshot-gallery handlers re-call
  `screenshots()` on every tap and resolve a
  `screenshot_pick:<provider>:<index>` callback against that result, so
  the index only means what the starter tapped while the same cached
  list is still being served. Once the entry expires the provider may
  return a differently-ordered list and the starter gets a different
  image than the one they picked. Tuning `DEFAULT_TTL_SECONDS` down
  widens that window."""

import time
import weakref
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Concatenate, ParamSpec, TypeVar

import httpx
from loguru import logger

DEFAULT_TTL_SECONDS = 300.0  # 5 minutes — long enough to dedupe rapid
# re-taps within one setup session, short enough that nothing goes
# meaningfully stale, and long enough to cover a gallery-paging session
# (see the TTL note in the module docstring before shortening it).

_P = ParamSpec("_P")
_T = TypeVar("_T")

# One bound `.clear` method per `@cached()`-decorated function, so the
# test-only clear() below can wipe every private cache without needing
# a single shared dict (see module docstring for why each function
# gets its own instead).
_registered_clears: list[Callable[[], None]] = []


def cached(
    ttl: float = DEFAULT_TTL_SECONDS,
) -> Callable[
    [Callable[Concatenate[httpx.AsyncClient, _P], Awaitable[_T]]],
    Callable[Concatenate[httpx.AsyncClient, _P], Awaitable[_T]],
]:
    """Decorates an async function whose first parameter is the
    `httpx.AsyncClient` making the call. The cache key is built
    automatically from that client plus every other positional/keyword
    argument — nothing to hand-write per call site. Returns the cached
    value if it was stored less than `ttl` seconds ago, else calls the
    wrapped function, caches the result, and returns it.

    `Concatenate[httpx.AsyncClient, _P]` on both sides is what keeps the
    wrapped function's real signature visible to `ty` (a pre-commit gate
    and a CI job here): a plain `*args: object, **kwargs: object`
    wrapper preserved the return type but erased every parameter, so a
    dropped argument or typo'd keyword at any of the provider call sites
    in `search.py`/`screenshots.py`/`screenshot_gallery.py` type-checked
    clean. It also states "the first parameter is the client" in the
    type system instead of only in this docstring."""

    def decorator(
        func: Callable[Concatenate[httpx.AsyncClient, _P], Awaitable[_T]],
    ) -> Callable[Concatenate[httpx.AsyncClient, _P], Awaitable[_T]]:
        # Resolved once here rather than per call: `func` is typed as a
        # callable, and not every callable is a function carrying a
        # `__qualname__` — the real decorated ones all are.
        func_name = getattr(func, "__qualname__", repr(func))

        # Private to this one decorated function — see module docstring
        # for why that's what makes every read below fully typed as
        # `_T`, with no cast needed to unbox it. Weakly keyed by client,
        # so a collected client takes its whole sub-cache with it.
        own_cache: weakref.WeakKeyDictionary[
            httpx.AsyncClient, dict[tuple[object, ...], tuple[float, _T]]
        ] = weakref.WeakKeyDictionary()
        _registered_clears.append(own_cache.clear)

        @wraps(func)
        async def wrapper(client: httpx.AsyncClient, *args: _P.args, **kwargs: _P.kwargs) -> _T:
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            per_client = own_cache.setdefault(client, {})
            entry = per_client.get(key)
            if entry is not None:
                cached_at, value = entry
                if now - cached_at < ttl:
                    logger.debug("Cache hit for {} {}", func_name, key)
                    return value

            value = await func(client, *args, **kwargs)
            per_client[key] = (now, value)
            return value

        return wrapper

    return decorator


def clear() -> None:
    """Test-only escape hatch — see test_cache.py's autouse fixture."""
    for clear_one in _registered_clears:
        clear_one()
