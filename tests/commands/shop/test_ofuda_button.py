from unittest.mock import AsyncMock

from sqlalchemy import select

from nani_pix_bot.commands.shop import menu as shop_menu
from nani_pix_bot.commands.shop.keyboards import SHOP_OFUDA_PREFIX
from nani_pix_bot.models.enums import ConsumableKind, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.halloween import ArmedOfuda
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.cosmetics import consumables
from tests.commands.shop.helpers import make_context, make_query, seed_game, set_currency, tap


def _hold(session_factory, n: int, user_id: int = 2) -> None:
    set_currency(session_factory, user_id, 1000)
    with session_factory() as session:
        consumables.add(session, user_id, ConsumableKind.OFUDA, "test", n)
        session.commit()


def _render(session_factory, game_id: int):
    with session_factory() as session:
        game = session.get(Game, game_id)
        buyer = session.get(Player, 2)
        assert game is not None
        assert buyer is not None
        return shop_menu.render_shop(session, game, buyer, "en")


def _rows(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _armed(session_factory) -> list[ArmedOfuda]:
    with session_factory() as session:
        return list(session.scalars(select(ArmedOfuda)))


def _ofuda_query(game_id: int):
    query = make_query(f"{SHOP_OFUDA_PREFIX}{game_id}")
    query.edit_message_text = AsyncMock()
    return query


async def test_menu_shows_the_arm_button_only_to_a_holder(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 1000)
    _, markup = _render(session_factory, game_id)
    assert f"{SHOP_OFUDA_PREFIX}{game_id}" not in _rows(markup)
    _hold(session_factory, 1)
    _, markup = _render(session_factory, game_id)
    last = markup.inline_keyboard[-1][0]
    assert last.callback_data == f"{SHOP_OFUDA_PREFIX}{game_id}"
    assert last.text == i18n.t("shop.ofuda_button", "en", count=1)
    assert "1" in last.text


async def test_menu_hides_the_button_on_hard_mode(session_factory) -> None:
    game_id = seed_game(session_factory, hard_mode=True)
    _hold(session_factory, 1)
    _, markup = _render(session_factory, game_id)
    assert f"{SHOP_OFUDA_PREFIX}{game_id}" not in _rows(markup)


async def test_menu_hides_the_button_once_armed(session_factory) -> None:
    game_id = seed_game(session_factory)
    _hold(session_factory, 1)
    await tap(make_context(session_factory), _ofuda_query(game_id))
    _, markup = _render(session_factory, game_id)
    assert f"{SHOP_OFUDA_PREFIX}{game_id}" not in _rows(markup)


async def test_button_survives_an_empty_clue_menu(session_factory, monkeypatch) -> None:
    game_id = seed_game(session_factory)
    _hold(session_factory, 1)
    monkeypatch.setattr(shop, "offers", lambda *a, **k: [])
    text, markup = _render(session_factory, game_id)
    assert i18n.t("shop.nothing_left", "en") in text
    assert _rows(markup) == [f"{SHOP_OFUDA_PREFIX}{game_id}"]


async def test_tap_arms_and_refreshes_the_menu(session_factory) -> None:
    game_id = seed_game(session_factory)
    _hold(session_factory, 1)
    query = _ofuda_query(game_id)
    await tap(make_context(session_factory), query)
    assert len(_armed(session_factory)) == 1
    query.answer.assert_awaited_once_with(i18n.t("shop.ofuda_armed", "en"), show_alert=True)
    query.edit_message_text.assert_awaited_once()
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert f"{SHOP_OFUDA_PREFIX}{game_id}" not in _rows(markup)


async def test_second_tap_is_refused_already_armed(session_factory) -> None:
    game_id = seed_game(session_factory)
    _hold(session_factory, 1)
    context = make_context(session_factory)
    await tap(context, _ofuda_query(game_id))
    query = _ofuda_query(game_id)
    await tap(context, query)
    assert len(_armed(session_factory)) == 1
    query.answer.assert_awaited_once_with(
        i18n.t("shop.ofuda_refused.already_armed", "en"), show_alert=True
    )
    query.edit_message_text.assert_not_awaited()


async def test_tap_for_a_finished_round_is_refused(session_factory) -> None:
    game_id = seed_game(session_factory, status=GameStatus.WON)
    _hold(session_factory, 1)
    query = _ofuda_query(game_id)
    await tap(make_context(session_factory), query)
    assert _armed(session_factory) == []
    query.answer.assert_awaited_once_with(
        i18n.t("shop.ofuda_refused.no_game", "en"), show_alert=True
    )


async def test_tap_by_a_non_member_is_refused(session_factory) -> None:
    game_id = seed_game(session_factory)
    _hold(session_factory, 1)
    await tap(make_context(session_factory, member=False), _ofuda_query(game_id))
    assert _armed(session_factory) == []


async def test_malformed_ofuda_data_is_ignored(session_factory) -> None:
    query = make_query(f"{SHOP_OFUDA_PREFIX}abc")
    await tap(make_context(session_factory), query)
    query.answer.assert_awaited_once_with()
