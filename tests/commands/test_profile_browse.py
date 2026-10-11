from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram import InputMediaPhoto, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.commands.profile import browse
from nani_pix_bot.commands.profile import card as card_module
from nani_pix_bot.commands.profile import data as cb
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cards import CARD_SIZE

ME = 1
# Ten players: me plus nine more, so the list has two pages of 8.
NAMES = {ME: "me", 2: "amy", 3: "bob", 4: "cat", 5: "dan", 6: "eve", 7: "fay", 8: "gus", 9: "hal"}
NAMES |= {10: "ivy"}
ROSTER = sorted(NAMES, key=lambda i: NAMES[i])  # amy bob cat dan eve fay gus hal ivy me


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def avatar(monkeypatch) -> AsyncMock:
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(card_module, "fetch_avatar", fetch)
    return fetch


@pytest.fixture(autouse=True)
def players(session_factory) -> None:
    with session_factory() as session:
        session.add_all(Player(telegram_user_id=i, username=n) for i, n in NAMES.items())
        session.commit()


def _query(data: str, *, tapper: int = ME) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.from_user.id = tapper
    query.answer = AsyncMock()
    query.edit_message_media = AsyncMock()
    query.edit_message_caption = AsyncMock()
    return query


async def _tap(session_factory, query: MagicMock) -> None:
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    await browse.profile_callback(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))


def _data(markup) -> list[list[str]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


# --- callback data -----------------------------------------------------------


def test_callback_data_round_trips() -> None:
    assert cb.parse(cb.card_data(1, 2, "n")) == ("o", [1, 2, "n"])
    assert cb.parse(cb.list_data(1, 3)) == ("l", [1, 3])


@pytest.mark.parametrize(
    "data",
    [
        "prof:x:1:2",  # unknown action
        "prof:o:1:2",  # too few fields
        "prof:l:1:2:3",  # too many
        "prof:o:1:abc:n",  # not a number
        "prof:o:1:-2:n",  # negative
        f"prof:o:1:{MAX_ID + 1}:n",  # past the DB's integer range
        "prof:o:1:2:z",  # unknown "via"
    ],
)
def test_malformed_callback_data_is_rejected(data: str) -> None:
    assert cb.parse(data) is None


def test_the_longest_callback_data_fits_telegrams_64_bytes() -> None:
    assert len(cb.card_data(MAX_ID, MAX_ID, "m").encode()) <= 64


# --- keyboards ---------------------------------------------------------------


def test_card_keyboard_wraps_around_at_both_ends() -> None:
    first, last = ROSTER[0], ROSTER[-1]
    rows = _data(browse.card_keyboard(ROSTER, browse.Viewing(ME, first, private=False), "EN"))
    assert rows[0] == [cb.card_data(ME, last, "p"), cb.card_data(ME, ROSTER[1], "n")]


def test_card_keyboard_players_button_opens_the_page_the_player_is_on() -> None:
    on_second_page = ROSTER[8]
    rows = _data(
        browse.card_keyboard(ROSTER, browse.Viewing(ME, on_second_page, private=False), "EN")
    )
    assert rows[-1] == [cb.list_data(ME, 1)]


def test_a_lone_player_gets_no_prev_next() -> None:
    assert _data(browse.card_keyboard([ME], browse.Viewing(ME, ME, private=False), "EN")) == [
        [cb.list_data(ME, 0)]
    ]


def test_list_view_pages_names_and_ends_with_my_card(session) -> None:
    session.add_all(Player(telegram_user_id=i, username=n) for i, n in NAMES.items())
    session.flush()
    caption, markup, page = browse.list_view(session, ROSTER, ME, 0, "EN")
    rows = markup.inline_keyboard
    assert caption == i18n.t("profile.list", "EN")
    assert page == 0
    assert [row[0].text for row in rows[:8]] == [f"@{NAMES[i]}" for i in ROSTER[:8]]
    assert rows[0][0].callback_data == cb.card_data(ME, ROSTER[0], "l")
    assert [b.text for b in rows[8]] == ["1/2", i18n.t("achievements.next_page", "EN")]
    assert rows[-1][0].callback_data == cb.card_data(ME, ME, "m")


def test_a_stale_page_is_clamped_to_the_last_one(session) -> None:
    session.add_all(Player(telegram_user_id=i, username=n) for i, n in NAMES.items())
    session.flush()
    _caption, markup, page = browse.list_view(session, ROSTER, ME, 99, "EN")
    assert page == 1
    assert [row[0].text for row in markup.inline_keyboard[:2]] == ["@ivy", "@me"]


# --- taps --------------------------------------------------------------------


async def test_next_swaps_the_photo_for_the_next_players_card(session_factory, log_records) -> None:
    query = _query(cb.card_data(ME, 3, "n"))
    await _tap(session_factory, query)

    (media,) = query.edit_message_media.await_args.args
    assert isinstance(media, InputMediaPhoto)
    assert "@bob" in media.caption
    keyboard = query.edit_message_media.await_args.kwargs["reply_markup"]
    assert _data(keyboard)[0] == [cb.card_data(ME, 2, "p"), cb.card_data(ME, 4, "n")]
    query.answer.assert_awaited_once_with()
    info = [r for r in log_records if r.level == "INFO"]
    assert info[-1].extra["target_id"] == 3
    assert info[-1].extra["via"] == "▶"


async def test_the_card_photo_is_a_full_profile_card(session_factory) -> None:
    query = _query(cb.card_data(ME, 3, "l"))
    await _tap(session_factory, query)

    (media,) = query.edit_message_media.await_args.args
    assert Image.open(BytesIO(media.media.input_file_content)).size == CARD_SIZE


async def test_players_turns_the_caption_into_the_list(session_factory, log_records) -> None:
    query = _query(cb.list_data(ME, 1))
    await _tap(session_factory, query)

    kwargs = query.edit_message_caption.await_args.kwargs
    assert kwargs["caption"] == i18n.t("profile.list", "EN")
    assert _data(kwargs["reply_markup"])[0] == [cb.card_data(ME, ROSTER[8], "l")]
    query.edit_message_media.assert_not_awaited()
    assert any(r.level == "INFO" and "player list" in r.message for r in log_records)


async def test_someone_elses_tap_is_refused_with_an_alert(session_factory, log_records) -> None:
    query = _query(cb.card_data(ME, 3, "n"), tapper=5)
    await _tap(session_factory, query)

    query.answer.assert_awaited_once_with(i18n.t("profile.not_yours", "EN"), show_alert=True)
    query.edit_message_media.assert_not_awaited()
    warning = [r for r in log_records if r.level == "WARNING"][-1]
    assert warning.extra["owner_id"] == ME


async def test_a_player_who_is_gone_gets_an_alert(session_factory, log_records) -> None:
    with session_scope(session_factory) as session:
        session.delete(session.get(Player, 3))
    query = _query(cb.card_data(ME, 3, "n"))
    await _tap(session_factory, query)

    query.answer.assert_awaited_once_with(i18n.t("profile.gone", "EN"), show_alert=True)
    query.edit_message_media.assert_not_awaited()
    assert any(r.level == "WARNING" and r.extra.get("target_id") == 3 for r in log_records)


async def test_malformed_data_is_answered_and_ignored(session_factory, log_records) -> None:
    query = _query("prof:o:1:x:n")
    await _tap(session_factory, query)

    query.answer.assert_awaited_once_with()
    query.edit_message_media.assert_not_awaited()
    query.edit_message_caption.assert_not_awaited()
    assert any(r.level == "WARNING" and "malformed" in r.message for r in log_records)


async def test_a_failed_edit_still_answers_the_tap(session_factory, log_records) -> None:
    query = _query(cb.card_data(ME, 3, "n"))
    query.edit_message_media.side_effect = BadRequest("Message is not modified")
    await _tap(session_factory, query)

    query.answer.assert_awaited_once_with()
    assert any(r.level == "WARNING" and "could not switch" in r.message for r in log_records)
