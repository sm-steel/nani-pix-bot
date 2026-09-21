import httpx
import pytest

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.game import autostart
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tenrai import TenraiResult


def test_roll_overthrow_is_deterministic_via_the_rng(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart.secrets, "randbelow", lambda _n: 0)
    assert autostart.roll_overthrow() is True

    monkeypatch.setattr(autostart.secrets, "randbelow", lambda _n: 9_999)
    assert autostart.roll_overthrow() is False


_SHIKI_RESULT = ShikimoriResult(
    shikimori_id=1, title_romaji="Frieren", title_english=None, title_russian=None, synonyms=[]
)
_TENRAI_RESULT = TenraiResult(
    tenrai_id=2, title_romaji="Frieren", title_english=None, title_native=None, synonyms=[]
)


class _StubAsyncClient(httpx.AsyncClient):
    """A minimal stand-in for httpx.AsyncClient — gather_pick()'s own
    helpers never inspect the client beyond passing it through to the
    (monkeypatched) provider functions below, so no real transport is
    needed. Subclasses the real thing (rather than a bare stand-in
    class) purely so `ty check` accepts it wherever gather_pick's
    `httpx.AsyncClient`-typed parameters are passed one of these; the
    real `__init__` is skipped since no actual transport is ever
    used."""

    def __init__(self) -> None:
        pass


def _patch_stub_get(monkeypatch: pytest.MonkeyPatch, *, content: bytes = b"bytes") -> None:
    """Stubs _StubAsyncClient.get() to answer every download with `content`
    — every gather_pick() test that reaches _download_screenshot needs
    this same fake, extracted here rather than repeated per test (issue
    #159's final review)."""

    async def fake_get(url, **kwargs):
        # `request=` is required: a bare httpx.Response with no request
        # attached raises from raise_for_status() ("the request instance
        # has not been set on this response"), which _download_screenshot
        # calls on every fetch.
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))

    # staticmethod(...): a plain function assigned to a class attribute is
    # a descriptor, so `client.get(url)` would otherwise bind `client` as
    # the fake's first positional argument (self) ahead of `url` — this
    # keeps the fake's signature exactly `(url, **kwargs)`, matching
    # httpx.AsyncClient.get's own call shape.
    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(fake_get), raising=False)


async def test_gather_pick_succeeds_on_shikimori_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_random_anime(client):
        return _SHIKI_RESULT

    async def fake_screenshots(client, shikimori_id):
        assert shikimori_id == 1
        return ["https://shikimori.io/x/a.jpg", "https://shikimori.io/x/b.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", fake_random_anime)
    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    _patch_stub_get(monkeypatch)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient())

    assert pick is not None
    assert pick.anime.source == Provider.SHIKIMORI
    assert pick.screenshot.provider == Provider.SHIKIMORI
    assert pick.screenshot.provider_id == 1
    assert pick.screenshot.image_bytes_a == b"bytes"
    assert pick.screenshot.image_bytes_b == b"bytes"


async def test_gather_pick_falls_back_to_tenrai_when_shikimori_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_random_anime(client):
        raise RuntimeError("Shikimori is down")

    async def fake_tenrai_random(client):
        return _TENRAI_RESULT

    async def fake_screenshots(client, tenrai_id):
        assert tenrai_id == 2
        return ["https://cdn.myanimelist.net/x/a.jpg", "https://cdn.myanimelist.net/x/b.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", failing_random_anime)
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.random_anime", fake_tenrai_random)
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.screenshots", fake_screenshots)

    _patch_stub_get(monkeypatch)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient())

    assert pick is not None
    assert pick.anime.source == Provider.TENRAI
    assert pick.screenshot.provider == Provider.TENRAI


async def test_gather_pick_retries_a_different_anime_when_no_screenshots_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    picks = [_SHIKI_RESULT, ShikimoriResult(9, "Second", None, None, [])]
    calls = {"n": 0}

    async def fake_random_anime(client):
        result = picks[calls["n"]]
        calls["n"] += 1
        return result

    async def fake_screenshots(client, shikimori_id):
        return (
            []
            if shikimori_id == 1
            else ["https://shikimori.io/x/b.jpg", "https://shikimori.io/x/c.jpg"]
        )

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", fake_random_anime)
    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    _patch_stub_get(monkeypatch)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient())

    assert calls["n"] == 2
    assert pick is not None
    assert pick.screenshot.provider_id == 9


async def test_gather_pick_gives_up_after_the_attempt_limit_with_no_db_touch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_none(client):
        return None

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", always_none)
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.random_anime", always_none)

    calls = {"n": 0}

    async def counting_random_anime(client):
        calls["n"] += 1
        return

    monkeypatch.setattr(
        "nani_pix_bot.services.search.shikimori.random_anime", counting_random_anime
    )

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient())

    assert pick is None
    assert calls["n"] == autostart.AUTOSTART_ATTEMPT_LIMIT


async def test_gather_pick_rejects_an_explicit_rated_tenrai_pick_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenrai's /random/anime request already sends sfw=true, but this
    rating check is a second, backstop layer (unlike Shikimori's
    server-side censored: true) — a "rating": "Rx - Hentai" pick must
    still be rejected the same way a screenshot-less pick is: treated as
    a failed attempt so gather_pick's loop retries with a different
    anime, never surfacing the explicit pick to a caller (issue #159)."""

    async def failing_shikimori_random(client):
        raise RuntimeError("Shikimori is down")

    tenrai_picks = [
        TenraiResult(
            tenrai_id=1,
            title_romaji="Explicit Anime",
            title_english=None,
            title_native=None,
            synonyms=[],
            rating="Rx - Hentai",
        ),
        TenraiResult(
            tenrai_id=2,
            title_romaji="Safe Anime",
            title_english=None,
            title_native=None,
            synonyms=[],
            rating="PG-13 - Teens 13 or older",
        ),
    ]
    calls = {"n": 0}

    async def fake_tenrai_random(client):
        result = tenrai_picks[calls["n"]]
        calls["n"] += 1
        return result

    async def fake_screenshots(client, tenrai_id):
        assert tenrai_id == 2
        return ["https://cdn.myanimelist.net/x/a.jpg", "https://cdn.myanimelist.net/x/b.jpg"]

    monkeypatch.setattr(
        "nani_pix_bot.services.search.shikimori.random_anime", failing_shikimori_random
    )
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.random_anime", fake_tenrai_random)
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.screenshots", fake_screenshots)

    _patch_stub_get(monkeypatch)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient())

    assert calls["n"] == 2
    assert pick is not None
    assert pick.anime.source == Provider.TENRAI
    assert pick.screenshot.provider_id == 2


async def test_gather_pick_falls_through_provider_order_when_first_provider_lacks_a_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-provider-first fallback (_screenshot_provider_order): Shikimori
    identified the anime but only has 1 screenshot for it — not enough
    for a hard-mode pair — so the loop falls through to Tenrai, which has
    2. This is the same anime the whole way through (no retry), just a
    different screenshot provider — distinct from the "retry a different
    anime" tests above."""

    async def fake_random_anime(client):
        return _SHIKI_RESULT

    async def fake_shikimori_screenshots(client, shikimori_id):
        assert shikimori_id == 1
        return ["https://shikimori.io/x/only-one.jpg"]

    async def fake_tenrai_search(client, title):
        return [_TENRAI_RESULT]

    async def fake_tenrai_screenshots(client, tenrai_id):
        assert tenrai_id == 2
        return ["https://cdn.myanimelist.net/x/a.jpg", "https://cdn.myanimelist.net/x/b.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", fake_random_anime)
    monkeypatch.setattr(
        "nani_pix_bot.services.search.shikimori.screenshots", fake_shikimori_screenshots
    )
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.search", fake_tenrai_search)
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.screenshots", fake_tenrai_screenshots)

    _patch_stub_get(monkeypatch)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient())

    assert pick is not None
    assert pick.anime.source == Provider.SHIKIMORI
    assert pick.screenshot.provider == Provider.TENRAI
    assert pick.screenshot.provider_id == 2


async def test_fetch_screenshot_url_pair_returns_two_distinct_urls_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_screenshots(client, shikimori_id):
        assert shikimori_id == 1
        return [
            "https://shikimori.io/x/a.jpg",
            "https://shikimori.io/x/b.jpg",
            "https://shikimori.io/x/c.jpg",
        ]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    pair = await autostart._fetch_screenshot_url_pair(_StubAsyncClient(), Provider.SHIKIMORI, 1)

    assert pair is not None
    url_a, url_b = pair
    assert url_a != url_b
    assert {url_a, url_b} <= {
        "https://shikimori.io/x/a.jpg",
        "https://shikimori.io/x/b.jpg",
        "https://shikimori.io/x/c.jpg",
    }


@pytest.mark.parametrize("urls", [[], ["https://shikimori.io/x/a.jpg"]])
async def test_fetch_screenshot_url_pair_returns_none_with_fewer_than_two_urls(
    monkeypatch: pytest.MonkeyPatch, urls: list[str]
) -> None:
    async def fake_screenshots(client, shikimori_id):
        return urls

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    pair = await autostart._fetch_screenshot_url_pair(_StubAsyncClient(), Provider.SHIKIMORI, 1)

    assert pair is None


async def test_fetch_screenshot_url_pair_returns_none_and_logs_warning_on_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_screenshots(client, shikimori_id):
        raise RuntimeError("Shikimori screenshot endpoint is down")

    logged: list[tuple] = []
    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", failing_screenshots)
    monkeypatch.setattr(autostart.logger, "warning", lambda *args: logged.append(args))

    pair = await autostart._fetch_screenshot_url_pair(_StubAsyncClient(), Provider.SHIKIMORI, 1)

    assert pair is None
    assert logged


async def test_try_provider_returns_pick_with_distinct_images_when_both_downloads_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_screenshots(client, shikimori_id):
        assert shikimori_id == 1
        return ["https://shikimori.io/x/a.jpg", "https://shikimori.io/x/b.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    async def fake_get(url, **kwargs):
        content = b"image-a" if url.endswith("a.jpg") else b"image-b"
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))

    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(fake_get), raising=False)

    anime = autostart.AnimePick(result=_SHIKI_RESULT, source=Provider.SHIKIMORI)
    clients = autostart.SearchClients(
        search=_StubAsyncClient(), tmdb=_StubAsyncClient(), tenrai=_StubAsyncClient()
    )
    pick = await autostart._try_provider(clients, anime, Provider.SHIKIMORI, "Frieren")

    assert pick is not None
    assert pick.provider == Provider.SHIKIMORI
    assert pick.provider_id == 1
    assert pick.image_bytes_a != pick.image_bytes_b
    assert {pick.image_bytes_a, pick.image_bytes_b} == {b"image-a", b"image-b"}


async def test_try_provider_returns_none_when_pair_fetch_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_screenshots(client, shikimori_id):
        return ["https://shikimori.io/x/only-one.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    anime = autostart.AnimePick(result=_SHIKI_RESULT, source=Provider.SHIKIMORI)
    clients = autostart.SearchClients(
        search=_StubAsyncClient(), tmdb=_StubAsyncClient(), tenrai=_StubAsyncClient()
    )
    pick = await autostart._try_provider(clients, anime, Provider.SHIKIMORI, "Frieren")

    assert pick is None


async def test_try_provider_discards_first_download_when_second_download_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_screenshots(client, shikimori_id):
        return ["https://shikimori.io/x/a.jpg", "https://shikimori.io/x/b.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    calls = {"n": 0}

    async def flaky_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.HTTPError("second download failed")
        return httpx.Response(200, content=b"image-a", request=httpx.Request("GET", url))

    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(flaky_get), raising=False)

    anime = autostart.AnimePick(result=_SHIKI_RESULT, source=Provider.SHIKIMORI)
    clients = autostart.SearchClients(
        search=_StubAsyncClient(), tmdb=_StubAsyncClient(), tenrai=_StubAsyncClient()
    )
    pick = await autostart._try_provider(clients, anime, Provider.SHIKIMORI, "Frieren")

    assert pick is None
    assert calls["n"] == 2
