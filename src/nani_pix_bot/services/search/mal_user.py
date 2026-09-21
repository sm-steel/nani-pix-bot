"""MAL's OFFICIAL API client (api.myanimelist.net / myanimelist.net's
own OAuth endpoints) — NOT this package's Tenrai/AniList/Shikimori/TMDB
modules, which are all public-catalog-only. This module is the only
one in services/search/ that does per-player OAuth; it deliberately
does NOT register a Provider.search_module/screenshot_module delegator
(see models/enums.py) — "MAL list" is not a Provider, the same way
"manual" isn't (see docs/superpowers/specs/2026-09-21-mal-account-linking-design.md).

FIELD NAMES BELOW (paging.next, list_status.status, node.main_picture)
FOLLOW MAL API v2's DOCUMENTED CONVENTIONS BUT WERE NOT LIVE-VERIFIED
FROM THIS DEVELOPMENT ENVIRONMENT: network egress to myanimelist.net /
api.myanimelist.net was unavailable from this sandbox (TLS handshake
timeout — a prior investigation earlier in this project confirmed the
same), and completing a real MAL OAuth consent flow additionally
requires a human logging into a real MAL account in a browser. This is
deliberately deferred to the plan's own pre-ship "Final Verification"
manual step rather than blocking this task — spot-check the actual
`GET /v2/users/@me/animelist` response shape against a real account
before shipping and adjust this module if reality differs.

RETRY BEHAVIOR: every network call below is wrapped through
services/search/http_retry.py's `request_with_retry`, the same shared
transient-failure retry plumbing anilist.py/shikimori.py/tenrai.py/
tmdb.py use (via graphql.py/rest.py respectively) — MAL isn't the one
exception. `request_with_retry` retries up to `MAX_RATE_LIMIT_RETRIES`
times on a 429 (honoring `Retry-After`), raises `httpx.HTTPStatusError`
via `response.raise_for_status()` on any other non-2xx status, and
raises `RuntimeError` if retries are exhausted.

Both token endpoints take their client_id/client_secret pair bundled
as a single `MalOAuthApp` rather than as two loose kwargs each — see
that dataclass's own docstring for why. This is a deliberate deviation
from this task's brief's sketch, made to resolve a real `qlty smells`
"too many parameters" finding on `exchange_code_for_tokens` (6 loose
kwargs) by changing the actual code rather than loosening the check,
per this repo's own rule against the latter.

The token endpoints (`exchange_code_for_tokens`/`refresh_tokens`) treat
a rejected request (MAL answers 400 with `{"error": "invalid_grant"}` for a bad/expired
code or a revoked refresh token) as an *expected* failure, so they
catch `httpx.HTTPStatusError` and return None rather than let it
propagate — matching this package's "expected failure returns None"
convention used throughout services/search/ (e.g. rest.py's
`fetch_by_id` on a 404). `fetch_list` has no such expected-failure
case — a non-2xx there means the caller handed it an invalid/expired
access token, which is a caller bug (see its own docstring), so it's
left to propagate like every other provider's HTTP errors."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from loguru import logger

from nani_pix_bot.services.search import http_retry

MAL_AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
MAL_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"  # noqa: S105 - URL, not a secret
MAL_ANIMELIST_URL = "https://api.myanimelist.net/v2/users/@me/animelist"

_SERVICE_NAME = "MAL"

# RFC 7636's code_verifier must be 43-128 characters; MAL only supports
# code_challenge_method=plain (no SHA256 transform), so code_challenge
# sent in the authorize URL is code_verifier verbatim.
_VERIFIER_BYTES = 64
_STATE_BYTES = 32


def generate_state_and_verifier() -> tuple[str, str]:
    """A fresh (state, code_verifier) pair for one /linkmal attempt —
    both cryptographically random, url-safe. Callers persist both via
    services/mal_link.py's upsert_pending_link before building the
    authorize URL, and the player never has to see or type `state`."""
    return secrets.token_urlsafe(_STATE_BYTES), secrets.token_urlsafe(_VERIFIER_BYTES)


def build_authorize_url(
    *, client_id: str, redirect_uri: str, state: str, code_verifier: str
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "state": state,
        "redirect_uri": redirect_uri,
        "code_challenge": code_verifier,
        "code_challenge_method": "plain",
    }
    return f"{MAL_AUTHORIZE_URL}?{urlencode(params)}"


@dataclass(frozen=True)
class MalTokenResponse:
    access_token: str
    refresh_token: str
    expires_at: datetime


@dataclass(frozen=True)
class MalOAuthApp:
    """This bot's own MAL developer-app identity — client_id, client_secret,
    and redirect_uri are all fixed per bot deployment (sourced from Config,
    never per-call), grouped the same way rest.py's `RestApi`/graphql.py's
    `GraphQLApi` group each provider's own per-request identity there,
    rather than threading three more loose keyword arguments through
    every call below. (Deviates from this task's brief, which had these
    as separate kwargs on `exchange_code_for_tokens`/`refresh_tokens` —
    that shape put `exchange_code_for_tokens` at 6 parameters, which
    `qlty smells` correctly flags; this is that finding fixed by
    changing the actual code rather than loosening the check, per this
    repo's own rule against the latter — see the task-5 report.)"""

    client_id: str
    client_secret: str
    redirect_uri: str


async def _post_token_request(
    client: httpx.AsyncClient, app: MalOAuthApp, extra: dict[str, str], *, context: str
) -> MalTokenResponse | None:
    """Shared body for exchange_code_for_tokens/refresh_tokens — both
    POST to the same endpoint and parse the same response shape,
    differing only in which grant_type/credential they send (`extra`).

    A rejected request (bad/expired code, revoked refresh token) comes
    back from MAL as a 400 — `request_with_retry` turns that into an
    `httpx.HTTPStatusError` via `raise_for_status()`, which is caught
    here and translated into None, this package's "expected failure"
    convention, rather than propagating as an exception."""
    data = {"client_id": app.client_id, "client_secret": app.client_secret, **extra}

    async def make_request() -> httpx.Response:
        return await client.post(MAL_TOKEN_URL, data=data)

    try:
        response = await http_retry.request_with_retry(
            make_request, service_name=_SERVICE_NAME, context=context
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "MAL token endpoint rejected the request ({}): {} {}",
            context,
            exc.response.status_code,
            exc.response.text,
        )
        return None

    body = response.json()
    return MalTokenResponse(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=body["expires_in"]),
    )


async def exchange_code_for_tokens(
    client: httpx.AsyncClient,
    app: MalOAuthApp,
    *,
    code: str,
    code_verifier: str,
) -> MalTokenResponse | None:
    """The token endpoint, called once a player pastes back an
    authorization code. None (not an exception) on a rejected/expired
    code — the caller (commands/dm_start/mal_browse.py) replies with an
    error and leaves the pending_mal_link row in place so the player
    can paste again, matching this package's "expected failure returns
    None" convention."""
    return await _post_token_request(
        client,
        app,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": app.redirect_uri,
            "code_verifier": code_verifier,
        },
        context="token exchange",
    )


async def refresh_tokens(
    client: httpx.AsyncClient, app: MalOAuthApp, *, refresh_token: str
) -> MalTokenResponse | None:
    """None (not an exception) if the refresh token is expired/revoked
    — the caller treats this the same as an unlinked player and prompts
    /linkmal again, per the spec."""
    return await _post_token_request(
        client,
        app,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        context="token refresh",
    )


@dataclass(frozen=True)
class MalAnimeListEntry:
    mal_id: int
    title: str
    image_url: str | None
    status: str


@dataclass(frozen=True)
class MalAnimeListPage:
    entries: list[MalAnimeListEntry]
    has_more: bool
    next_offset: int


async def fetch_list(
    client: httpx.AsyncClient, access_token: str, *, offset: int = 0, limit: int = 20
) -> MalAnimeListPage:
    """One page of the authenticated player's own anime list — every
    status included (the spec's confirmed "show all, tagged" decision,
    not filtered to completed-only). Raises on a transport/HTTP error
    (this endpoint requires a valid, non-expired access_token — the
    caller is responsible for refreshing before calling this, see
    services/mal_link.py's get_credentials + refresh_tokens above).
    Unlike the token endpoints, an HTTP error here isn't caught as an
    expected outcome — a non-2xx means the caller handed this an
    invalid access token, which is a caller bug, not something a
    player-facing "MAL list" flow is meant to recover from silently."""

    async def make_request() -> httpx.Response:
        return await client.get(
            MAL_ANIMELIST_URL,
            params={"fields": "list_status", "limit": limit, "offset": offset},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    response = await http_retry.request_with_retry(
        make_request, service_name=_SERVICE_NAME, context=f"fetch list offset={offset}"
    )
    body = response.json()

    entries = []
    for item in body.get("data", []):
        node = item.get("node", {})
        picture = node.get("main_picture") or {}
        entries.append(
            MalAnimeListEntry(
                mal_id=node["id"],
                title=node["title"],
                image_url=picture.get("medium"),
                status=item.get("list_status", {}).get("status", "unknown"),
            )
        )

    has_more = bool(body.get("paging", {}).get("next"))
    return MalAnimeListPage(entries=entries, has_more=has_more, next_offset=offset + limit)
