from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from nani_pix_bot.services.search import http_retry


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


async def test_request_with_retry_parses_http_date_retry_after_and_bounds_it(
    monkeypatch: pytest.MonkeyPatch,
    records: list[tuple[str, str]],
) -> None:
    # RFC 9110 permits Retry-After to be an HTTP-date instead of
    # delta-seconds; Cloudflare (fronting AniList, see anilist.py) commonly
    # sends this form. A date far in the future should be parsed correctly
    # (not crash) but still bounded, since honoring it literally would park
    # the handler for hours.
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", fake_sleep)
    http_date = format_datetime(datetime.now(UTC) + timedelta(hours=2))
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429, headers={"Retry-After": http_date})
        return _error_response(200)

    await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert sleeps == [http_retry.MAX_RETRY_AFTER_SECONDS]
    warnings = [message for level, message in records if level == "WARNING"]
    assert any(http_date in w for w in warnings)


async def test_request_with_retry_falls_back_to_default_on_non_numeric_garbage(
    monkeypatch: pytest.MonkeyPatch,
    records: list[tuple[str, str]],
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429, headers={"Retry-After": "banana"})
        return _error_response(200)

    await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert sleeps == [http_retry.DEFAULT_RETRY_AFTER_SECONDS]
    warnings = [message for level, message in records if level == "WARNING"]
    assert any("banana" in w for w in warnings)


async def test_request_with_retry_clamps_negative_retry_after_to_zero(
    monkeypatch: pytest.MonkeyPatch,
    records: list[tuple[str, str]],
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429, headers={"Retry-After": "-5"})
        return _error_response(200)

    await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert sleeps == [0.0]
    warnings = [message for level, message in records if level == "WARNING"]
    assert any("-5" in w for w in warnings)


async def test_request_with_retry_clamps_absurdly_large_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    records: list[tuple[str, str]],
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429, headers={"Retry-After": "86400"})
        return _error_response(200)

    await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert sleeps == [http_retry.MAX_RETRY_AFTER_SECONDS]
    warnings = [message for level, message in records if level == "WARNING"]
    assert any("86400" in w for w in warnings)


@pytest.mark.parametrize("header_value", ["nan", "inf", "-inf"])
async def test_request_with_retry_falls_back_to_default_on_non_finite_retry_after(
    header_value: str,
    monkeypatch: pytest.MonkeyPatch,
    records: list[tuple[str, str]],
) -> None:
    """`float()` parses "nan"/"inf"/"-inf" without raising, so these reach
    the clamp expression as a non-finite `delay` rather than the
    unparseable-garbage branch. Pinning this by test (rather than trusting
    the clamp's accidental NaN-ordering behavior) is the point: a future
    refactor of `max(0.0, min(delay, MAX_RETRY_AFTER_SECONDS))` could
    silently reintroduce `asyncio.sleep(nan)`, which hangs forever."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(http_retry.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def make_request() -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _error_response(429, headers={"Retry-After": header_value})
        return _error_response(200)

    await http_retry.request_with_retry(make_request, service_name="Test", context="ctx")

    assert sleeps == [http_retry.DEFAULT_RETRY_AFTER_SECONDS]
    warnings = [message for level, message in records if level == "WARNING"]
    assert any(header_value in w for w in warnings)
