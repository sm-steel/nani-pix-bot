import httpx
import pytest

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.game import autostart
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult


def test_roll_overthrow_is_deterministic_via_the_rng(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart.secrets, "randbelow", lambda _n: 0)
    assert autostart.roll_overthrow() is True

    monkeypatch.setattr(autostart.secrets, "randbelow", lambda _n: 9_999)
    assert autostart.roll_overthrow() is False


_SHIKI_RESULT = ShikimoriResult(
    shikimori_id=1, title_romaji="Frieren", title_english=None, title_russian=None, synonyms=[]
)
_JIKAN_RESULT = JikanResult(
    jikan_id=2, title_romaji="Frieren", title_english=None, title_native=None, synonyms=[]
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


async def test_gather_pick_succeeds_on_shikimori_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_random_anime(client):
        return _SHIKI_RESULT

    async def fake_screenshots(client, shikimori_id):
        assert shikimori_id == 1
        return ["https://shikimori.io/x/a.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", fake_random_anime)
    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    async def fake_get(url, **kwargs):
        # `request=` is required: a bare httpx.Response with no request
        # attached raises from raise_for_status() ("the request instance
        # has not been set on this response"), which _download_screenshot
        # calls on every fetch.
        return httpx.Response(200, content=b"bytes", request=httpx.Request("GET", url))

    # staticmethod(...): a plain function assigned to a class attribute is
    # a descriptor, so `client.get(url)` would otherwise bind `client` as
    # the fake's first positional argument (self) ahead of `url` — this
    # keeps the fake's signature exactly `(url, **kwargs)`, matching
    # httpx.AsyncClient.get's own call shape.
    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(fake_get), raising=False)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient())

    assert pick is not None
    assert pick.anime.source == Provider.SHIKIMORI
    assert pick.screenshot.provider == Provider.SHIKIMORI
    assert pick.screenshot.provider_id == 1
    assert pick.screenshot.image_bytes == b"bytes"


async def test_gather_pick_falls_back_to_jikan_when_shikimori_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_random_anime(client):
        raise RuntimeError("Shikimori is down")

    async def fake_jikan_random(client):
        return _JIKAN_RESULT

    async def fake_screenshots(client, jikan_id):
        assert jikan_id == 2
        return ["https://cdn.myanimelist.net/x/a.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", failing_random_anime)
    monkeypatch.setattr("nani_pix_bot.services.search.jikan.random_anime", fake_jikan_random)
    monkeypatch.setattr("nani_pix_bot.services.search.jikan.screenshots", fake_screenshots)

    async def fake_get(url, **kwargs):
        # `request=` is required: a bare httpx.Response with no request
        # attached raises from raise_for_status() ("the request instance
        # has not been set on this response"), which _download_screenshot
        # calls on every fetch.
        return httpx.Response(200, content=b"bytes", request=httpx.Request("GET", url))

    # staticmethod(...): a plain function assigned to a class attribute is
    # a descriptor, so `client.get(url)` would otherwise bind `client` as
    # the fake's first positional argument (self) ahead of `url` — this
    # keeps the fake's signature exactly `(url, **kwargs)`, matching
    # httpx.AsyncClient.get's own call shape.
    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(fake_get), raising=False)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient())

    assert pick is not None
    assert pick.anime.source == Provider.JIKAN
    assert pick.screenshot.provider == Provider.JIKAN


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
        return [] if shikimori_id == 1 else ["https://shikimori.io/x/b.jpg"]

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", fake_random_anime)
    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.screenshots", fake_screenshots)

    async def fake_get(url, **kwargs):
        # `request=` is required: a bare httpx.Response with no request
        # attached raises from raise_for_status() ("the request instance
        # has not been set on this response"), which _download_screenshot
        # calls on every fetch.
        return httpx.Response(200, content=b"bytes", request=httpx.Request("GET", url))

    # staticmethod(...): a plain function assigned to a class attribute is
    # a descriptor, so `client.get(url)` would otherwise bind `client` as
    # the fake's first positional argument (self) ahead of `url` — this
    # keeps the fake's signature exactly `(url, **kwargs)`, matching
    # httpx.AsyncClient.get's own call shape.
    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(fake_get), raising=False)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient())

    assert calls["n"] == 2
    assert pick is not None
    assert pick.screenshot.provider_id == 9


async def test_gather_pick_gives_up_after_the_attempt_limit_with_no_db_touch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_none(client):
        return None

    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", always_none)
    monkeypatch.setattr("nani_pix_bot.services.search.jikan.random_anime", always_none)

    calls = {"n": 0}

    async def counting_random_anime(client):
        calls["n"] += 1
        return

    monkeypatch.setattr(
        "nani_pix_bot.services.search.shikimori.random_anime", counting_random_anime
    )

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient())

    assert pick is None
    assert calls["n"] == autostart.AUTOSTART_ATTEMPT_LIMIT


async def test_gather_pick_rejects_an_explicit_rated_jikan_pick_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jikan's /random/anime endpoint has no server-side SFW filter
    (unlike Shikimori's censored: true) — a "rating": "Rx - Hentai" pick
    must be rejected the same way a screenshot-less pick is: treated as
    a failed attempt so gather_pick's loop retries with a different
    anime, never surfacing the explicit pick to a caller (issue #159)."""

    async def failing_shikimori_random(client):
        raise RuntimeError("Shikimori is down")

    jikan_picks = [
        JikanResult(
            jikan_id=1,
            title_romaji="Explicit Anime",
            title_english=None,
            title_native=None,
            synonyms=[],
            rating="Rx - Hentai",
        ),
        JikanResult(
            jikan_id=2,
            title_romaji="Safe Anime",
            title_english=None,
            title_native=None,
            synonyms=[],
            rating="PG-13 - Teens 13 or older",
        ),
    ]
    calls = {"n": 0}

    async def fake_jikan_random(client):
        result = jikan_picks[calls["n"]]
        calls["n"] += 1
        return result

    async def fake_screenshots(client, jikan_id):
        assert jikan_id == 2
        return ["https://cdn.myanimelist.net/x/a.jpg"]

    monkeypatch.setattr(
        "nani_pix_bot.services.search.shikimori.random_anime", failing_shikimori_random
    )
    monkeypatch.setattr("nani_pix_bot.services.search.jikan.random_anime", fake_jikan_random)
    monkeypatch.setattr("nani_pix_bot.services.search.jikan.screenshots", fake_screenshots)

    async def fake_get(url, **kwargs):
        # `request=` is required: a bare httpx.Response with no request
        # attached raises from raise_for_status() ("the request instance
        # has not been set on this response"), which _download_screenshot
        # calls on every fetch.
        return httpx.Response(200, content=b"bytes", request=httpx.Request("GET", url))

    # staticmethod(...): a plain function assigned to a class attribute is
    # a descriptor, so `client.get(url)` would otherwise bind `client` as
    # the fake's first positional argument (self) ahead of `url` — this
    # keeps the fake's signature exactly `(url, **kwargs)`, matching
    # httpx.AsyncClient.get's own call shape.
    monkeypatch.setattr(_StubAsyncClient, "get", staticmethod(fake_get), raising=False)

    pick = await autostart.gather_pick(_StubAsyncClient(), _StubAsyncClient())

    assert calls["n"] == 2
    assert pick is not None
    assert pick.anime.source == Provider.JIKAN
    assert pick.screenshot.provider_id == 2
