import httpx
import pytest

from nani_pix_bot.services.search import shikimori, tenrai
from nani_pix_bot.services.search.tags import AnimeTag
from nani_pix_bot.services.seasons.tags import TagClients, TagsUnavailableError, fetch_tags

_TENRAI_TAGS = [AnimeTag("genre", "Romance", 22)]
_SHIKIMORI_TAGS = [AnimeTag("genre", "Romance", None)]


@pytest.fixture
def clients() -> TagClients:
    return TagClients(shikimori=httpx.AsyncClient(), tenrai=httpx.AsyncClient())


def _fake(calls: list[str], name: str, outcomes: list):
    queue = list(outcomes)

    async def fake(client, mal_id):
        calls.append(name)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return fake


async def test_tenrai_answer_leaves_shikimori_untouched(monkeypatch, clients) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tenrai, "get_tags", _fake(calls, "tenrai", [_TENRAI_TAGS]))
    monkeypatch.setattr(shikimori, "get_tags", _fake(calls, "shikimori", [_SHIKIMORI_TAGS]))

    assert await fetch_tags(clients, 4224) == _TENRAI_TAGS
    assert calls == ["tenrai"]


async def test_unknown_anime_is_none_without_fallback(monkeypatch, clients) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tenrai, "get_tags", _fake(calls, "tenrai", [None]))
    monkeypatch.setattr(shikimori, "get_tags", _fake(calls, "shikimori", [_SHIKIMORI_TAGS]))

    assert await fetch_tags(clients, 4224) is None
    assert calls == ["tenrai"]


async def test_tenrai_retried_once_then_succeeds(monkeypatch, clients) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tenrai, "get_tags", _fake(calls, "tenrai", [httpx.ReadTimeout("x"), _TENRAI_TAGS])
    )
    monkeypatch.setattr(shikimori, "get_tags", _fake(calls, "shikimori", [_SHIKIMORI_TAGS]))

    assert await fetch_tags(clients, 4224) == _TENRAI_TAGS
    assert calls == ["tenrai", "tenrai"]


async def test_tenrai_failing_twice_falls_back_to_shikimori(monkeypatch, clients) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tenrai,
        "get_tags",
        _fake(calls, "tenrai", [httpx.ReadTimeout("x"), httpx.ReadError("y")]),
    )
    monkeypatch.setattr(shikimori, "get_tags", _fake(calls, "shikimori", [_SHIKIMORI_TAGS]))

    assert await fetch_tags(clients, 4224) == _SHIKIMORI_TAGS
    assert calls == ["tenrai", "tenrai", "shikimori"]


async def test_both_failing_raises_tags_unavailable(monkeypatch, clients) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tenrai,
        "get_tags",
        _fake(calls, "tenrai", [httpx.ReadTimeout("x"), httpx.ReadTimeout("x")]),
    )
    monkeypatch.setattr(shikimori, "get_tags", _fake(calls, "shikimori", [RuntimeError("down")]))

    with pytest.raises(TagsUnavailableError):
        await fetch_tags(clients, 4224)
    assert calls == ["tenrai", "tenrai", "shikimori"]


async def test_malformed_tenrai_body_is_retried_then_falls_back(monkeypatch, clients) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None})

    broken = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    calls: list[str] = []
    monkeypatch.setattr(shikimori, "get_tags", _fake(calls, "shikimori", [_SHIKIMORI_TAGS]))

    result = await fetch_tags(TagClients(shikimori=clients.shikimori, tenrai=broken), 4224)

    assert result == _SHIKIMORI_TAGS
    assert calls == ["shikimori"]
