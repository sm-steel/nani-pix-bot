import httpx
import pytest

from nani_pix_bot.services import http_retry


def _error_response(status_code: int, **kwargs) -> httpx.Response:
    # raise_for_status() needs a `request` attached, unlike the
    # MockTransport-backed responses anilist.py/shikimori.py's own tests use.
    return httpx.Response(status_code, request=httpx.Request("GET", "http://test"), **kwargs)


async def test_request_with_retry_returns_response_on_first_success() -> None:
    async def make_request() -> httpx.Response:
        return _error_response(200)

    response = await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert response.status_code == 200


async def test_request_with_retry_retries_after_429_then_succeeds() -> None:
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429, headers={"Retry-After": "0"})
        return _error_response(200)

    response = await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert response.status_code == 200
    assert calls["n"] == 2


async def test_request_with_retry_uses_default_retry_after_when_header_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429)  # no Retry-After header
        return _error_response(200)

    monkeypatch.setattr(http_retry, "DEFAULT_RETRY_AFTER_SECONDS", 2.5)

    await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert sleeps == [2.5]


async def test_request_with_retry_raises_runtime_error_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http_retry, "MAX_RATE_LIMIT_RETRIES", 2)

    async def make_request() -> httpx.Response:
        return _error_response(429, headers={"Retry-After": "0"})

    with pytest.raises(RuntimeError, match="rate limit"):
        await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")


async def test_request_with_retry_propagates_non_429_error_status() -> None:
    async def make_request() -> httpx.Response:
        return _error_response(500)

    with pytest.raises(httpx.HTTPStatusError):
        await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")
