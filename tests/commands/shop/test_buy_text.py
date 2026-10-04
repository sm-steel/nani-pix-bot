from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram.error import TimedOut

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import settings
from tests.commands.shop.helpers import (
    balance,
    make_context,
    make_query,
    purchases,
    seed_game,
    set_currency,
    tap,
)


def _sent_texts(context) -> list[str]:
    return [call.kwargs["text"] for call in context.bot.send_message.await_args_list]


async def test_buying_first_letter_dms_the_letter_and_charges(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    dm = context.bot.send_message.await_args_list[0]
    assert dm.kwargs["chat_id"] == 2
    assert dm.kwargs["parse_mode"] == "HTML"
    assert "<b>S</b>" in dm.kwargs["text"]
    assert "(romaji)" in dm.kwargs["text"]
    assert dm.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("shop:share:")
    assert balance(session_factory) == 80
    notice = context.bot.send_message.await_args_list[1]
    assert notice.kwargs["chat_id"] == 555
    assert notice.kwargs["message_thread_id"] == 7
    assert "Buyer Name" in notice.kwargs["text"]
    assert "first letter" in notice.kwargs["text"]


async def test_second_tap_on_same_item_is_not_charged(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory)
    data = f"shop:buy:{game_id}:first_letter"

    await tap(context, make_query(data))
    second = make_query(data)
    await tap(context, second)

    second.answer.assert_awaited_once()
    assert second.answer.await_args.kwargs["show_alert"] is True
    assert "already have" in second.answer.await_args.args[0]
    assert balance(session_factory) == 80
    assert len(purchases(session_factory)) == 1


async def test_buy_for_finished_game_is_refused_without_charge(session_factory) -> None:
    game_id = seed_game(session_factory, status=GameStatus.WON)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert "That round is over" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert purchases(session_factory) == []
    assert balance(session_factory) == 100
    context.bot.send_message.assert_not_awaited()


async def test_failed_delivery_refunds_the_purchase(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert balance(session_factory) == 100
    assert purchases(session_factory) == []
    with session_factory() as session:
        transfers = list(session.scalars(select(CurrencyTransfer)))
    assert any(t.reverses_id is not None for t in transfers)
    assert "refunded" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    # only the failed DM was attempted; no topic notice for a refunded buy
    assert context.bot.send_message.await_count == 1


async def test_letter_after_shape_resends_the_filled_shape(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:title_shape"))
    context.bot.send_message.reset_mock()
    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    dms = [c for c in context.bot.send_message.await_args_list if c.kwargs["chat_id"] == 2]
    assert len(dms) == 2
    assert "<code>S " in dms[1].kwargs["text"]
    assert dms[1].kwargs.get("reply_markup") is None


async def test_ru_group_falls_back_and_names_the_field(session_factory) -> None:
    game_id = seed_game(session_factory, title_romaji=None, title_english="Frieren")
    set_currency(session_factory, 2, 100)
    with session_factory() as session:
        settings.set_language(session, "ru")
        session.commit()
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:last_letter"))

    assert "(английское)" in _sent_texts(context)[0]


async def test_unaffordable_is_refused_with_balance(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 5)
    context = make_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert "you have 5" in query.answer.await_args.args[0]
    assert balance(session_factory) == 5
    assert purchases(session_factory) == []


async def test_non_member_is_refused(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory, member=False)
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert "member of the group" in query.answer.await_args.args[0]
    assert balance(session_factory) == 100


async def test_malformed_data_is_answered_silently_without_charge(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    for data in (
        "shop:buy:abc:first_letter",
        f"shop:buy:{game_id}:bogus",
        f"shop:buy:{game_id}:first_letter:extra",
        "shop:buy:1",
    ):
        context = make_context(session_factory)
        query = make_query(data)
        await tap(context, query)
        query.answer.assert_awaited_once_with()
        context.bot.send_message.assert_not_awaited()
    assert balance(session_factory) == 100


async def test_unimplemented_branches_answer_silently(session_factory) -> None:
    query = make_query("shop:share:1")
    await tap(make_context(session_factory), query)
    query.answer.assert_awaited_once_with()


async def test_notice_failure_does_not_break_the_purchase(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    context = make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=[MagicMock(), TimedOut()])
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert balance(session_factory) == 80
    assert len(purchases(session_factory)) == 1
    query.answer.assert_awaited_once_with()
