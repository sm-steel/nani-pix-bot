"""Tests for commands.dm_start.mal_browse — the player-facing MAL flow:
pasting back an authorization code, tapping the 6th "My MAL List"
method button, and browsing/picking from the resulting list.

Hardcoded test token values trigger S105/S106 (Possible hardcoded
password), a false positive in test data — same suppression as
tests/commands/test_mal_link.py."""
# ruff: noqa: S105, S106

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import mal_browse, search
from nani_pix_bot.commands.dm_start.keyboards import MAL_METHOD_CALLBACK_DATA
from nani_pix_bot.models.enums import GameStatus, Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, mal_link
from nani_pix_bot.services.search.tenrai import TenraiResult

_ENCRYPTION_KEY = Fernet.generate_key().decode()

_FRIEREN_TENRAI = TenraiResult(
    tenrai_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren at the Funeral"],
)


def _list_body(*, count: int = 2, has_next: bool = False) -> dict:
    return {
        "data": [
            {
                "node": {
                    "id": 52991 + index,
                    "title": f"Anime {index}",
                    "main_picture": {"medium": "https://example.com/pic.jpg"},
                },
                "list_status": {"status": "completed"},
            }
            for index in range(count)
        ],
        "paging": {"next": "https://example.com/next"} if has_next else {},
    }


def _mal_transport(
    *,
    token_status: int = 200,
    token_body: dict | None = None,
    list_body: dict | None = None,
    requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A MAL API double covering both endpoints this module talks to:
    the OAuth token endpoint (code exchange + refresh) and the
    authenticated anime-list endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if "oauth2/token" in str(request.url):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json=token_body
                or {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            )
        return httpx.Response(200, json=list_body if list_body is not None else _list_body())

    return httpx.MockTransport(handler)


def _make_context(session_factory, *, transport: httpx.MockTransport | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "mal_client_id": "cid",
        "mal_client_secret": "csecret",
        "mal_redirect_uri": "https://example.com/cb",
        "mal_token_encryption_key": _ENCRYPTION_KEY,
        "mal_client": httpx.AsyncClient(transport=transport or _mal_transport()),
        "tenrai_client": MagicMock(),
    }
    context.bot.send_message = AsyncMock()
    context.job_queue = MagicMock()
    return context


def _make_text_update(*, user_id: int = 1, text: str = "the-pasted-code") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = "private"
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _add_player(session_factory, *, user_id: int = 1) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=user_id))
        session.commit()


def _add_pending_link(session_factory, *, user_id: int = 1) -> None:
    with session_factory() as session:
        mal_link.upsert_pending_link(session, user_id, state="s", code_verifier="v")
        session.commit()


def _add_credentials(session_factory, *, user_id: int = 1, expires_in_seconds: int = 3600) -> None:
    with session_factory() as session:
        mal_link.upsert_credentials(
            session,
            user_id,
            data=mal_link.CredentialsData(
                encryption_key=_ENCRYPTION_KEY,
                access_token="stored-access-token",
                refresh_token="stored-refresh-token",
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
                mal_username="player",
            ),
        )
        session.commit()


def _create_setup_game(session_factory, *, user_id: int = 1) -> None:
    with session_factory() as session:
        game_service.create_setup_game(session, starter_id=user_id, original_image=None)
        session.commit()


# --- the pasted-back authorization code (via search_text_handler) ---


async def test_search_text_handler_consumes_a_pasted_code_with_no_active_game(
    session_factory,
) -> None:
    _add_player(session_factory)
    _add_pending_link(session_factory)

    update = _make_text_update()
    context = _make_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    with session_factory() as session:
        assert mal_link.get_pending_link(session, 1) is None
        credentials = mal_link.get_credentials(session, 1, encryption_key=_ENCRYPTION_KEY)
        assert credentials is not None
        assert credentials.access_token == "at"


async def test_search_text_handler_leaves_pending_row_on_a_failed_exchange(
    session_factory,
) -> None:
    _add_player(session_factory)
    _add_pending_link(session_factory)

    update = _make_text_update(text="a-bad-code")
    context = _make_context(session_factory, transport=_mal_transport(token_status=400))

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0] == i18n.t("mal_link.code_rejected", "en")
    with session_factory() as session:
        assert mal_link.get_pending_link(session, 1) is not None  # NOT deleted
        assert mal_link.get_credentials(session, 1, encryption_key=_ENCRYPTION_KEY) is None


async def test_search_text_handler_falls_through_to_existing_logic_when_no_pending_link(
    session_factory,
) -> None:
    """A plain DM with no pending MAL link and no active game must behave
    exactly as it did before this feature existed — a regression guard,
    not a new-feature test."""
    _add_player(session_factory)

    update = _make_text_update(text="some random message")
    context = _make_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_not_awaited()


async def test_a_pasted_code_mid_method_pick_opens_the_list_browser(session_factory) -> None:
    """The whole point of the 6th button linking mid-/newgame: once the
    code lands, the player goes straight to their list rather than
    dead-ending on a bare "linked!" confirmation."""
    _add_player(session_factory)
    _add_pending_link(session_factory)
    _create_setup_game(session_factory)

    update = _make_text_update()
    context = _make_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0] == i18n.t("dm_start.mal_list_prompt", "en")
    keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == "mal_list_pick:52991"


async def test_a_pasted_code_without_a_setup_game_just_confirms(session_factory) -> None:
    _add_player(session_factory)
    _add_pending_link(session_factory)

    update = _make_text_update()
    context = _make_context(session_factory)

    await search.search_text_handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    assert update.message.reply_text.await_args.args[0] == i18n.t("mal_link.linked", "en")


# --- the 6th method-picker button ---


async def test_method_tap_opens_the_list_browser_when_already_linked(session_factory) -> None:
    _add_player(session_factory)
    _add_credentials(session_factory)
    _create_setup_game(session_factory)

    update = _make_callback_update(data=MAL_METHOD_CALLBACK_DATA)
    context = _make_context(session_factory)

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "dm_start.mal_list_prompt", "en"
    )
    with session_factory() as session:
        # "mal_list" is not a Provider — the tap must never land in the
        # source column the other five methods write to (its type
        # doesn't include that value).
        assert session.query(Game).filter_by(starter_id=1).one().source != "mal_list"


async def test_method_tap_starts_linking_when_not_linked(session_factory) -> None:
    _add_player(session_factory)
    _create_setup_game(session_factory)

    update = _make_callback_update(data=MAL_METHOD_CALLBACK_DATA)
    context = _make_context(session_factory)

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    reply = update.callback_query.edit_message_text.await_args.args[0]
    assert "myanimelist.net/v1/oauth2/authorize" in reply
    context.job_queue.run_once.assert_called_once()
    with session_factory() as session:
        assert mal_link.get_pending_link(session, 1) is not None


async def test_method_tap_restarts_linking_when_the_refresh_token_is_rejected(
    session_factory,
) -> None:
    """A stored-but-dead link (refresh token expired/revoked) is treated
    as unlinked, per the spec — the tap walks the player through linking
    again rather than erroring at them."""
    _add_player(session_factory)
    _add_credentials(session_factory, expires_in_seconds=-60)
    _create_setup_game(session_factory)

    update = _make_callback_update(data=MAL_METHOD_CALLBACK_DATA)
    context = _make_context(session_factory, transport=_mal_transport(token_status=400))

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    reply = update.callback_query.edit_message_text.await_args.args[0]
    assert "myanimelist.net/v1/oauth2/authorize" in reply
    with session_factory() as session:
        assert mal_link.get_pending_link(session, 1) is not None


# --- token refresh ---


async def test_an_expired_access_token_is_refreshed_before_fetching(session_factory) -> None:
    _add_player(session_factory)
    _add_credentials(session_factory, expires_in_seconds=-60)
    _create_setup_game(session_factory)

    requests: list[httpx.Request] = []
    update = _make_callback_update(data=MAL_METHOD_CALLBACK_DATA)
    context = _make_context(session_factory, transport=_mal_transport(requests=requests))

    await search.method_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert "oauth2/token" in str(requests[0].url)  # refreshed first
    assert requests[1].headers["Authorization"] == "Bearer at"  # then fetched with the new token
    with session_factory() as session:
        credentials = mal_link.get_credentials(session, 1, encryption_key=_ENCRYPTION_KEY)
        assert credentials is not None
        assert credentials.access_token == "at"
        assert credentials.mal_username == "player"  # preserved across a refresh


async def test_a_rejected_refresh_is_treated_as_unlinked(session_factory) -> None:
    _add_player(session_factory)
    _add_credentials(session_factory, expires_in_seconds=-60)
    _create_setup_game(session_factory)

    update = _make_callback_update(data="mal_list_page:10")
    context = _make_context(session_factory, transport=_mal_transport(token_status=400))

    await mal_browse.mal_list_page_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "mal_link.not_linked_anymore", "en"
    )


# --- paging ---


async def test_page_callback_fetches_the_requested_offset(session_factory) -> None:
    _add_player(session_factory)
    _add_credentials(session_factory)

    requests: list[httpx.Request] = []
    update = _make_callback_update(data="mal_list_page:10")
    context = _make_context(
        session_factory,
        transport=_mal_transport(list_body=_list_body(has_next=True), requests=requests),
    )

    await mal_browse.mal_list_page_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once()
    assert requests[0].url.params["offset"] == "10"
    keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    paging_row = keyboard.inline_keyboard[-1]
    assert [button.callback_data for button in paging_row] == [
        "mal_list_page:0",
        "mal_list_page:12",
    ]


async def test_an_empty_list_says_so(session_factory) -> None:
    _add_player(session_factory)
    _add_credentials(session_factory)

    update = _make_callback_update(data="mal_list_page:0")
    context = _make_context(
        session_factory, transport=_mal_transport(list_body=_list_body(count=0))
    )

    await mal_browse.mal_list_page_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "dm_start.mal_list_empty", "en"
    )


# --- picking an entry ---


async def test_pick_stages_the_tenrai_result_and_opens_the_screenshot_picker(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A MAL-list pick is identification-by-catalog, exactly like any
    other search-and-pick: it lands in game_service.stage_result and
    then in the shared screenshot-source picker, not in a bespoke
    "download one random screenshot" path."""
    _add_player(session_factory)
    _add_credentials(session_factory)
    _create_setup_game(session_factory)
    get_by_id_mock = AsyncMock(return_value=_FRIEREN_TENRAI)
    monkeypatch.setattr(mal_browse.tenrai, "get_by_id", get_by_id_mock)

    update = _make_callback_update(data="mal_list_pick:52991")
    context = _make_context(session_factory)

    await mal_browse.mal_list_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    get_by_id_mock.assert_awaited_once_with(context.bot_data["tenrai_client"], 52991)
    context.bot.send_message.assert_awaited_once()
    update.callback_query.answer.assert_awaited_once()
    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "dm_start.identification_staged", "en"
    )
    with session_factory() as session:
        fetched = session.query(Game).filter_by(starter_id=1).one()
        assert fetched.status == GameStatus.SETUP
        assert fetched.setup_step == SetupStep.PICKING_SCREENSHOT
        assert fetched.source == Provider.TENRAI.value
        assert fetched.tenrai_id == 52991
        assert fetched.title_english == "Frieren: Beyond Journey's End"


async def test_pick_says_so_when_the_anime_is_gone(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_player(session_factory)
    _create_setup_game(session_factory)
    monkeypatch.setattr(mal_browse.tenrai, "get_by_id", AsyncMock(return_value=None))

    update = _make_callback_update(data="mal_list_pick:52991")
    context = _make_context(session_factory)

    await mal_browse.mal_list_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert update.callback_query.edit_message_text.await_args.args[0] == i18n.t(
        "dm_start.mal_not_found_anymore", "en"
    )


async def test_pick_rejects_a_stale_tap_with_no_setup_game_left(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_player(session_factory)
    monkeypatch.setattr(mal_browse.tenrai, "get_by_id", AsyncMock(return_value=_FRIEREN_TENRAI))

    update = _make_callback_update(data="mal_list_pick:52991")
    context = _make_context(session_factory)

    await mal_browse.mal_list_pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("dm_start.setup_gone", "en"), show_alert=True
    )
    update.callback_query.edit_message_text.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()
