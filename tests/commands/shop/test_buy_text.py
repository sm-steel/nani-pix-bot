from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands.shop import callbacks
from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import settings


def _make_query(data: str, *, user_id: int = 2) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.from_user.username = f"user{user_id}"
    query.from_user.full_name = "Buyer Name"
    query.answer = AsyncMock()
    return query


def _make_context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_message = AsyncMock()
    status = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    return context


def _seed_game(session_factory, *, status=GameStatus.ACTIVE, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        fields = {
            "starter_id": 1,
            "original_image": b"file123",
            "status": status,
            "current_stage": PixelStage.STAGE_1,
            "title_romaji": "Sousou no Frieren",
        }
        fields.update(overrides)
        game = Game(**fields)
        session.add(game)
        session.commit()
        return game.id


def _set_currency(session_factory, user_id: int, amount: int) -> None:
    with session_factory() as session:
        player = session.get(Player, user_id)
        if player is None:
            player = Player(telegram_user_id=user_id)
            session.add(player)
        player.currency = amount
        session.commit()


def _balance(session_factory, user_id: int = 2) -> int:
    with session_factory() as session:
        player = session.get(Player, user_id)
        assert player is not None
        return player.currency


def _purchases(session_factory) -> list[CluePurchase]:
    with session_factory() as session:
        return list(session.scalars(select(CluePurchase)))


async def _tap(context, query) -> None:
    update = MagicMock()
    update.callback_query = query
    await callbacks.shop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )


def _sent_texts(context) -> list[str]:
    return [call.kwargs["text"] for call in context.bot.send_message.await_args_list]


async def test_buying_first_letter_dms_the_letter_and_charges(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory)

    await _tap(context, _make_query(f"shop:buy:{game_id}:first_letter"))

    dm = context.bot.send_message.await_args_list[0]
    assert dm.kwargs["chat_id"] == 2
    assert dm.kwargs["parse_mode"] == "HTML"
    assert "<b>S</b>" in dm.kwargs["text"]
    assert "(romaji)" in dm.kwargs["text"]
    assert dm.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("shop:share:")
    assert _balance(session_factory) == 80
    notice = context.bot.send_message.await_args_list[1]
    assert notice.kwargs["chat_id"] == 555
    assert notice.kwargs["message_thread_id"] == 7
    assert "Buyer Name" in notice.kwargs["text"]
    assert "first letter" in notice.kwargs["text"]


async def test_second_tap_on_same_item_is_not_charged(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory)
    data = f"shop:buy:{game_id}:first_letter"

    await _tap(context, _make_query(data))
    second = _make_query(data)
    await _tap(context, second)

    second.answer.assert_awaited_once()
    assert second.answer.await_args.kwargs["show_alert"] is True
    assert "already have" in second.answer.await_args.args[0]
    assert _balance(session_factory) == 80
    assert len(_purchases(session_factory)) == 1


async def test_buy_for_finished_game_is_refused_without_charge(session_factory) -> None:
    game_id = _seed_game(session_factory, status=GameStatus.WON)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory)
    query = _make_query(f"shop:buy:{game_id}:first_letter")

    await _tap(context, query)

    assert "That round is over" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert _purchases(session_factory) == []
    assert _balance(session_factory) == 100
    context.bot.send_message.assert_not_awaited()


async def test_failed_delivery_refunds_the_purchase(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=TimedOut())
    query = _make_query(f"shop:buy:{game_id}:first_letter")

    await _tap(context, query)

    assert _balance(session_factory) == 100
    assert _purchases(session_factory) == []
    with session_factory() as session:
        transfers = list(session.scalars(select(CurrencyTransfer)))
    assert any(t.reverses_id is not None for t in transfers)
    assert "refunded" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    # only the failed DM was attempted; no topic notice for a refunded buy
    assert context.bot.send_message.await_count == 1


async def test_letter_after_shape_resends_the_filled_shape(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory)

    await _tap(context, _make_query(f"shop:buy:{game_id}:title_shape"))
    context.bot.send_message.reset_mock()
    await _tap(context, _make_query(f"shop:buy:{game_id}:first_letter"))

    dms = [c for c in context.bot.send_message.await_args_list if c.kwargs["chat_id"] == 2]
    assert len(dms) == 2
    assert "<code>S " in dms[1].kwargs["text"]
    assert dms[1].kwargs.get("reply_markup") is None


async def test_ru_group_falls_back_and_names_the_field(session_factory) -> None:
    game_id = _seed_game(session_factory, title_romaji=None, title_english="Frieren")
    _set_currency(session_factory, 2, 100)
    with session_factory() as session:
        settings.set_language(session, "ru")
        session.commit()
    context = _make_context(session_factory)

    await _tap(context, _make_query(f"shop:buy:{game_id}:last_letter"))

    assert "(английское)" in _sent_texts(context)[0]


async def test_unaffordable_is_refused_with_balance(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 5)
    context = _make_context(session_factory)
    query = _make_query(f"shop:buy:{game_id}:first_letter")

    await _tap(context, query)

    assert "you have 5" in query.answer.await_args.args[0]
    assert _balance(session_factory) == 5
    assert _purchases(session_factory) == []


async def test_non_member_is_refused(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory, member=False)
    query = _make_query(f"shop:buy:{game_id}:first_letter")

    await _tap(context, query)

    assert "member of the group" in query.answer.await_args.args[0]
    assert _balance(session_factory) == 100


async def test_malformed_data_is_answered_silently_without_charge(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    for data in (
        "shop:buy:abc:first_letter",
        f"shop:buy:{game_id}:bogus",
        f"shop:buy:{game_id}:first_letter:extra",
        "shop:buy:1",
    ):
        context = _make_context(session_factory)
        query = _make_query(data)
        await _tap(context, query)
        query.answer.assert_awaited_once_with()
        context.bot.send_message.assert_not_awaited()
    assert _balance(session_factory) == 100


async def test_unimplemented_branches_answer_silently(session_factory) -> None:
    game_id = _seed_game(session_factory)
    for data in (f"shop:buy:{game_id}:tile", "shop:tile:1:3", "shop:share:1"):
        query = _make_query(data)
        await _tap(_make_context(session_factory), query)
        query.answer.assert_awaited_once_with()


async def test_notice_failure_does_not_break_the_purchase(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    context = _make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=[MagicMock(), TimedOut()])
    query = _make_query(f"shop:buy:{game_id}:first_letter")

    await _tap(context, query)

    assert _balance(session_factory) == 80
    assert len(_purchases(session_factory)) == 1
    query.answer.assert_awaited_once_with()
