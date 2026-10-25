"""A clue paid with a token posts a moment card to the topic; a 💠 one stays text."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nani_pix_bot.commands.shop import callbacks
from nani_pix_bot.jobs import moments
from nani_pix_bot.models.enums import ClueKind, ConsumableKind
from nani_pix_bot.services.cosmetics import consumables
from tests.commands.shop.helpers import (
    RICH,
    make_context,
    make_query,
    seed_game,
    set_currency,
    tap,
)


@pytest.fixture(autouse=True)
def _no_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(moments, "fetch_avatar", AsyncMock(return_value=None))


def _give_token(session_factory) -> None:
    with session_factory() as session:
        consumables.add(session, 2, ConsumableKind.CLUE_TICKET, why="test")
        session.commit()


def _context(session_factory):
    context = make_context(session_factory)
    context.bot.send_photo = AsyncMock()
    return context


def _topic_texts(context) -> list[str]:
    return [
        c.kwargs["text"]
        for c in context.bot.send_message.await_args_list
        if c.kwargs["chat_id"] == 555
    ]


async def test_a_token_paid_clue_posts_the_card_with_the_notice_as_caption(
    session_factory,
) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 0)
    _give_token(session_factory)
    context = _context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    sent = context.bot.send_photo.await_args.kwargs
    assert sent["chat_id"] == 555
    assert sent["message_thread_id"] == 7
    assert "Buyer Name" in sent["caption"]
    assert "first letter" in sent["caption"]
    assert _topic_texts(context) == []


async def test_a_paid_clue_keeps_the_plain_notice(session_factory) -> None:
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, RICH)
    context = _context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    context.bot.send_photo.assert_not_awaited()
    assert "first letter" in _topic_texts(context)[0]


async def test_a_failed_card_falls_back_to_the_plain_notice(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args, **_kwargs):
        raise OSError("no font")

    monkeypatch.setattr(moments.moment_cards, "charm_card", broken)
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 0)
    _give_token(session_factory)
    context = _context(session_factory)

    await tap(context, make_query(f"shop:buy:{game_id}:first_letter"))

    assert "first letter" in _topic_texts(context)[0]


async def test_a_zero_cost_clue_that_was_not_token_paid_keeps_the_plain_notice(
    session_factory,
) -> None:
    """The card follows `token_used`, not a zero amount."""
    context = _context(session_factory)
    user = MagicMock(id=2, full_name="Buyer Name")
    tap_ = MagicMock(user=user, game_id=1, kind=ClueKind.FIRST_LETTER)
    tap_.query.answer = AsyncMock()
    bought = callbacks._Bought(
        lang="en",
        purchase_id=1,
        amount=0,
        message="clue",
        followup=None,
        followup_purchase_id=None,
        buyer="Buyer Name",
        token_used=False,
    )

    await callbacks._announce(context, tap_, bought)

    context.bot.send_photo.assert_not_awaited()
    assert "first letter" in _topic_texts(context)[0]
