"""Tests for services.search.mal_user — hardcoded test token/secret values
trigger S105/S106 warnings (Possible hardcoded password), which are false
positives in test data."""
# ruff: noqa: S105, S106

from datetime import UTC, datetime

import httpx

from nani_pix_bot.services.search import mal_user
from nani_pix_bot.services.search.mal_user import MalAnimeListPage, MalOAuthApp

_APP = MalOAuthApp(client_id="cid", client_secret="csecret", redirect_uri="https://example.com/cb")


def test_generate_state_and_verifier_returns_two_distinct_url_safe_strings() -> None:
    state, verifier = mal_user.generate_state_and_verifier()
    assert state != verifier
    assert len(state) >= 32
    assert len(verifier) >= 43  # RFC 7636's minimum code_verifier length


def test_build_authorize_url_uses_plain_code_challenge_method() -> None:
    url = mal_user.build_authorize_url(
        client_id="cid", redirect_uri="https://example.com/cb", state="s1", code_verifier="v1"
    )
    assert url.startswith("https://myanimelist.net/v1/oauth2/authorize?")
    assert "client_id=cid" in url
    assert "state=s1" in url
    assert "code_challenge=v1" in url
    assert "code_challenge_method=plain" in url
    assert "response_type=code" in url


async def test_exchange_code_for_tokens_parses_a_successful_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/oauth2/token"
        body = request.read().decode()
        assert "grant_type=authorization_code" in body
        assert "code=the-code" in body
        assert "code_verifier=v1" in body
        return httpx.Response(
            200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await mal_user.exchange_code_for_tokens(
            client,
            _APP,
            code="the-code",
            code_verifier="v1",
        )

    assert result is not None
    assert result.access_token == "at"
    assert result.refresh_token == "rt"
    assert result.expires_at > datetime.now(UTC)


async def test_exchange_code_for_tokens_returns_none_on_a_rejected_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await mal_user.exchange_code_for_tokens(
            client,
            _APP,
            code="bad-code",
            code_verifier="v1",
        )

    assert result is None


async def test_refresh_tokens_parses_a_successful_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert "grant_type=refresh_token" in body
        assert "refresh_token=old-rt" in body
        return httpx.Response(
            200, json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await mal_user.refresh_tokens(client, _APP, refresh_token="old-rt")

    assert result is not None
    assert result.access_token == "new-at"


async def test_refresh_tokens_returns_none_on_a_revoked_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await mal_user.refresh_tokens(client, _APP, refresh_token="revoked-rt")

    assert result is None


async def test_fetch_list_parses_entries_and_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer at"
        assert request.url.path == "/v2/users/@me/animelist"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "node": {
                            "id": 52991,
                            "title": "Frieren: Beyond Journey's End",
                            "main_picture": {"medium": "https://example.com/52991.jpg"},
                        },
                        "list_status": {"status": "completed"},
                    },
                    {
                        "node": {"id": 1, "title": "Cowboy Bebop", "main_picture": None},
                        "list_status": {"status": "plan_to_watch"},
                    },
                ],
                "paging": {"next": "https://api.myanimelist.net/v2/users/@me/animelist?offset=20"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await mal_user.fetch_list(client, "at", offset=0, limit=20)

    assert isinstance(page, MalAnimeListPage)
    assert len(page.entries) == 2
    assert page.entries[0].mal_id == 52991
    assert page.entries[0].title == "Frieren: Beyond Journey's End"
    assert page.entries[0].image_url == "https://example.com/52991.jpg"
    assert page.entries[0].status == "completed"
    assert page.entries[1].image_url is None
    assert page.entries[1].status == "plan_to_watch"
    assert page.has_more is True
    assert page.next_offset == 20


async def test_fetch_list_has_more_is_false_on_the_last_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "paging": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await mal_user.fetch_list(client, "at", offset=20, limit=20)

    assert page.entries == []
    assert page.has_more is False
