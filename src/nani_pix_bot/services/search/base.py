"""Abstract base classes `Provider.search_module`/`screenshot_module`
(`models/enums.py`) resolve to at runtime — a thin per-provider adapter
class in each `services/search/*.py` module (`shikimori.py`'s
`_ShikimoriAdapter`/`.service`, and so on) subclasses one of these and
delegates every method straight to that module's own free functions.

Nominal (real subclassing), not `typing.Protocol`: a `Protocol` version
was tried first (issue #169) and `ty` couldn't correctly verify that a
provider's narrower return type (e.g. `ShikimoriResult | None`)
satisfies a Protocol method declared with the wider 4-provider union —
it checked the union's members individually rather than recognizing the
narrower type as a whole is assignable. Real ABC subclassing with
`@abstractmethod` doesn't hit that: overriding-method covariance is a
more mature/commonly-tested check than Protocol structural matching.

The adapters are deliberately thin wrappers, not a home for the actual
search logic: `services/search/cache.py`'s `@cache.cached()` decorator
requires its wrapped function's *first* parameter to be the
`httpx.AsyncClient` (see its own docstring — that's what lets it weakly
key its TTL cache per client, which is what gives every test its own
isolated cache for free). Moving `search`/`get_by_id`/`screenshots`
themselves into instance methods would put `self` first instead,
breaking that. Keeping them as free functions and only wrapping them
means every existing `monkeypatch.setattr(shikimori, "screenshots", ...)`
call across the test suite keeps working unmodified: an adapter method's
unqualified `screenshots(...)` call resolves against its module's
globals at call time, exactly like any other caller.

`Sequence`, not `list`, for `search()`'s return: `list` is invariant, so
a subclass override returning `list[ShikimoriResult]` would NOT satisfy
a base method declared to return `list[_SearchResult]` even though
`ShikimoriResult` is one of the members of `_SearchResult` — `Sequence`
is covariant, so this narrowing is accepted.

Only imported for `Provider.search_module`/`screenshot_module`'s type
annotations (`TYPE_CHECKING`-only in `enums.py`) and by each provider
module's adapter class — never anywhere else."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from nani_pix_bot.services.search.anilist import AniListResult
    from nani_pix_bot.services.search.jikan import JikanResult
    from nani_pix_bot.services.search.shikimori import ShikimoriResult
    from nani_pix_bot.services.search.tmdb import TMDBResult

    _SearchResult = AniListResult | ShikimoriResult | JikanResult | TMDBResult


class SearchModule(ABC):
    """Structural shape of a `services.search.*` provider capable of
    `search()`/`get_by_id()` — satisfied by all four providers' adapters,
    matching `Provider.search_module`'s own coverage of all four."""

    @abstractmethod
    async def search(self, client: httpx.AsyncClient, query: str, /) -> "Sequence[_SearchResult]":
        """No `limit` keyword here, unlike the free `search()` functions
        this delegates to: every real call site reaching a provider
        through `Provider.search_module`/`screenshot_module`
        (`commands/dm_start/`, `services/game/autostart.py`) always
        takes the free function's own default, so the adapter forwarding
        an explicit `limit=` on every call — even the default value —
        would force every test stub of a provider's `search()` to accept
        a keyword argument production code never actually passes."""
        ...

    @abstractmethod
    async def get_by_id(
        self, client: httpx.AsyncClient, provider_id: int, /
    ) -> "_SearchResult | None": ...


class ScreenshotModule(SearchModule, ABC):
    """Adds `screenshots()` — satisfied by shikimori/jikan/tmdb's
    adapters only, matching `Provider.screenshot_module`'s narrower
    coverage.

    Deliberately not folded into `SearchModule`: AniList has no
    screenshot endpoint, and `Provider.search_module` covers AniList too
    — requiring `.screenshots` there would make its adapter unable to
    subclass whichever base `search_module` declares."""

    @abstractmethod
    async def screenshots(self, client: httpx.AsyncClient, provider_id: int, /) -> list[str]: ...
