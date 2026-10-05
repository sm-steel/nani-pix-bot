from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram.error import TimedOut

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.services import settings
from nani_pix_bot.services.economy.config import EconomyKey
from tests.commands.shop.helpers import (
    PRICES,
    RICH,
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
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    dm = context.bot.send_message.await_args_list[0]
    assert dm.kwargs["chat_id"] == 2
    assert dm.kwargs["parse_mode"] == "HTML"
    assert "First letters of the titles" in dm.kwargs["text"]
    assert "• romaji: <b>S</b>" in dm.kwargs["text"]
    assert dm.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("shop:share:")
    assert balance(session_factory) == RICH - PRICES[EconomyKey.CLUE_FIRST_LETTER]
    notice = context.bot.send_message.await_args_list[1]
    assert notice.kwargs["chat_id"] == 555
    assert notice.kwargs["message_thread_id"] == 7
    assert "Buyer Name" in notice.kwargs["text"]
    assert "first letter" in notice.kwargs["text"]


async def test_second_tap_on_same_item_is_not_charged(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)
    data = f"shop:buy:{game_id}:first_letter"

    await tap(context, make_query(data))
    second = make_query(data)
    await tap(context, second)

    second.answer.assert_awaited_once()
    assert second.answer.await_args.kwargs["show_alert"] is True
    assert "already have" in second.answer.await_args.args[0]
    assert balance(session_factory) == RICH - PRICES[EconomyKey.CLUE_FIRST_LETTER]
    assert len(purchases(session_factory)) == 1


async def test_buy_for_finished_game_is_refused_without_charge(session_factory) -> None:
    game_id = seed_game(session_factory, status=GameStatus.WON)
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert "That round is over" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert purchases(session_factory) == []
    assert balance(session_factory) == RICH
    context.bot.send_message.assert_not_awaited()


async def test_failed_delivery_refunds_the_purchase(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert balance(session_factory) == RICH
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
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:title_shape"))
    context.bot.send_message.reset_mock()
    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    dms = [c for c in context.bot.send_message.await_args_list if c.kwargs["chat_id"] == 2]
    assert len(dms) == 2
    assert "<code>S " in dms[1].kwargs["text"]
    assert dms[1].kwargs["text"].count("<code>") == 1
    assert dms[1].kwargs.get("reply_markup") is None


async def test_ru_group_falls_back_and_names_the_field(session_factory) -> None:
    game_id = seed_game(session_factory, title_romaji=None, title_english="Frieren")
    set_currency(session_factory, 2, RICH)
    with session_factory() as session:
        settings.set_language(session, "ru")
        session.commit()
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:last_letter"))

    assert "• английское: <b>n</b>" in _sent_texts(context)[0]


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
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory, member=False)
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert "member of the group" in query.answer.await_args.args[0]
    assert balance(session_factory) == RICH


async def test_malformed_data_is_answered_silently_without_charge(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
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
    assert balance(session_factory) == RICH


async def test_malformed_callback_answers_silently(session_factory) -> None:
    query = make_query("shop:nonsense:1")
    await tap(make_context(session_factory), query)
    query.answer.assert_awaited_once_with()


async def test_notice_failure_does_not_break_the_purchase(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=[MagicMock(), TimedOut()])
    query = make_query(f"shop:buy:{game_id}:first_letter")

    await tap(context, query)

    assert balance(session_factory) == RICH - PRICES[EconomyKey.CLUE_FIRST_LETTER]
    assert len(purchases(session_factory)) == 1
    query.answer.assert_awaited_once_with()


async def _ru_three_title_game(session_factory) -> int:
    game_id = seed_game(
        session_factory,
        title_russian="Фрирен",
        title_romaji="Sousou no Frieren",
        title_english="Frieren",
    )
    set_currency(session_factory, 2, RICH)
    with session_factory() as session:
        settings.set_language(session, "ru")
        session.commit()
    return game_id


async def test_ru_first_letter_lists_every_title(session_factory) -> None:
    game_id = await _ru_three_title_game(session_factory)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    text = _sent_texts(context)[0]
    assert "Первые буквы названий:" in text
    assert "• русское: <b>Ф</b>" in text
    assert "• ромадзи: <b>S</b>" in text
    assert "• английское: <b>F</b>" in text


async def test_shape_has_a_block_per_title_with_bought_letters_filled(session_factory) -> None:
    game_id = await _ru_three_title_game(session_factory)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))
    await tap(context, make_query(f"shop:buy:{game_id}:title_shape"))

    text = _sent_texts(context)[-2]
    assert "Форма названий:" in text
    assert text.count("<code>") == 3
    assert "<code>Ф _ _ _ _ _</code> (6)" in text
    assert "<code>S _ _ _ _ _   _ _   _ _ _ _ _ _ _</code> (6, 2, 7)" in text
    assert "<code>F _ _ _ _ _ _</code> (7)" in text


async def test_successful_purchase_logs_delivery_and_notice_at_info(
    session_factory, log_records
) -> None:
    """Issue #228: the purchase line is logged when the 💠 is charged; the
    DM and the topic notice that follow need their own trace."""
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    info = [r for r in log_records if r.level == "INFO"]
    (dm,) = [r for r in info if r.message.startswith("DM'd the first_letter clue (purchase ")]
    assert (dm.extra["game_id"], dm.extra["kind"]) == (game_id, "first_letter")
    assert isinstance(dm.extra["purchase_id"], int)
    (notice,) = [
        r for r in info if r.message == "posted the first_letter clue-bought notice to the group"
    ]
    assert (notice.extra["game_id"], notice.extra["kind"]) == (game_id, "first_letter")


async def test_notice_failure_is_logged_with_its_game(session_factory, log_records) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=[MagicMock(), TimedOut()])

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    message = "could not post the first_letter clue-bought notice"
    (line,) = [r for r in log_records if r.message == message]
    assert (line.level, line.extra["game_id"]) == ("WARNING", game_id)
