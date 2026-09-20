"""The `SearchModule`/`ScreenshotModule` classes `Provider.search_module`/
`screenshot_module` (`models/enums.py`) return, giving those two
properties real static checking instead of bare `types.ModuleType` (whose
`__getattr__(...) -> Any` gave `ty` zero real checking on `.search`/
`.get_by_id`/`.screenshots` call sites — issue #169).

Concrete, generic delegators, not one subclass per provider: each holds a
reference to the actual `services/search/*.py` module it fronts (e.g.
`shikimori.py`'s `service = ScreenshotModule(sys.modules[__name__])`) and
forwards every call to that module's own free functions by *attribute
lookup on the live module object*, not a captured function reference —
`self._module.screenshots(...)` re-reads `self._module`'s current
`screenshots` attribute on every call, so
`monkeypatch.setattr(shikimori, "screenshots", ...)` (used throughout the
test suite) still takes effect. An earlier attempt gave each provider its
own subclass overriding `search`/`get_by_id`/`screenshots` with a
narrower per-provider return type; qlty flagged the four near-identical
subclasses as duplication, and rightly so — nothing here actually needs
that narrowing (every real caller already goes through the wide,
4-provider-union return declared below; `commands/dm_start/screenshots.py`
casts down to a narrower union at its own two call sites where it wants
one). Once nothing narrows per-provider, a single shared class covers
every provider with zero duplication.

Kept as free functions, not moved into instance methods:
`services/search/cache.py`'s `@cache.cached()` decorator requires its
wrapped function's *first* parameter to be the `httpx.AsyncClient` (see
its own docstring — that's what lets it weakly key its TTL cache per
client, which is what gives every test its own isolated cache for free).
Moving `search`/`get_by_id`/`screenshots` into instance methods would put
`self` first instead, breaking that.

Only imported for `Provider.search_module`/`screenshot_module`'s type
annotations (`TYPE_CHECKING`-only in `enums.py`) and by each provider
module's own `service = ...` line — never anywhere else.

**`self._module: ModuleType` means `ty` can't verify the module it's
constructed with actually has a matching `search`/`get_by_id`/
`screenshots` — a renamed or removed free function would otherwise only
surface as an `AttributeError` deep in a live request, the exact
"silent Any" failure mode this whole file exists to close.** `__init__`
closes that gap at runtime instead: it's called exactly once per
provider, at import time (`service = ScreenshotModule(sys.modules[
__name__])` executes as soon as e.g. `shikimori.py` is imported), so a
missing/non-callable method crashes the bot loudly on startup rather
than waiting for the one Telegram update that happens to hit it."""

from types import ModuleType
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nani_pix_bot.services.search.anilist import AniListResult
    from nani_pix_bot.services.search.jikan import JikanResult
    from nani_pix_bot.services.search.shikimori import ShikimoriResult
    from nani_pix_bot.services.search.tmdb import TMDBResult

    _SearchResult = AniListResult | ShikimoriResult | JikanResult | TMDBResult


def _require_callable(module: ModuleType, name: str) -> None:
    """Raises if `module` has no callable `name` attribute — see this
    module's docstring for why `__init__` checks this instead of trusting
    `ModuleType`. Only ever reads the attribute to verify it, never stores
    it: `self._module` (set by the caller) stays the live module
    reference every method call looks `name` up on fresh, so a test's
    later `monkeypatch.setattr(shikimori, name, ...)` still takes effect
    exactly as if this check didn't exist."""
    if not callable(getattr(module, name, None)):
        raise TypeError(f"{module.__name__!r} has no callable {name}()")


class SearchModule:
    """Delegates `search()`/`get_by_id()` to whichever `services.search.*`
    module it's constructed with — satisfied by all four providers,
    matching `Provider.search_module`'s own coverage of all four.

    No `limit` keyword on `search()` here, unlike the free functions this
    delegates to: every real call site reaching a provider through
    `Provider.search_module`/`screenshot_module` (`commands/dm_start/`,
    `services/game/autostart.py`) always takes the free function's own
    default, so forwarding an explicit `limit=` on every call — even the
    default value — would force every test stub of a provider's
    `search()` to accept a keyword argument production code never
    actually passes."""

    def __init__(self, module: ModuleType) -> None:
        _require_callable(module, "search")
        _require_callable(module, "get_by_id")
        self._module = module

    async def search(self, client: httpx.AsyncClient, query: str, /) -> "Sequence[_SearchResult]":
        return await self._module.search(client, query)

    async def get_by_id(
        self, client: httpx.AsyncClient, provider_id: int, /
    ) -> "_SearchResult | None":
        return await self._module.get_by_id(client, provider_id)


class ScreenshotModule(SearchModule):
    """Adds `screenshots()` — constructed only for shikimori/jikan/tmdb,
    matching `Provider.screenshot_module`'s narrower coverage (AniList has
    no screenshot endpoint)."""

    def __init__(self, module: ModuleType) -> None:
        super().__init__(module)
        _require_callable(module, "screenshots")

    async def screenshots(self, client: httpx.AsyncClient, provider_id: int, /) -> list[str]:
        return await self._module.screenshots(client, provider_id)
