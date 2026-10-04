from unittest.mock import AsyncMock

from telegram.error import TimedOut

from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import ClueKind, GameStatus
from nani_pix_bot.models.game import Game
from tests.commands.shop.helpers import (
    make_context,
    make_query,
    purchases,
    seed_game,
    set_currency,
    tap,
)


async def _bought(session_factory, kind: str = "first_letter") -> tuple[int, int]:
    """Buy a clue as user 2; returns (game_id, purchase_id)."""
    game_id = seed_game(session_factory)
    set_currency(session_factory, 2, 100)
    await tap(make_context(session_factory), make_query(f"shop:buy:{game_id}:{kind}"))
    return game_id, purchases(session_factory)[0].id


def _alert(query) -> str:
    assert query.answer.await_args.kwargs["show_alert"] is True
    return query.answer.await_args.args[0]


async def test_sharing_a_text_clue_posts_it_to_the_topic_once(session_factory) -> None:
    _, purchase_id = await _bought(session_factory)
    context = make_context(session_factory)
    first = make_query(f"shop:share:{purchase_id}")

    await tap(context, first)

    post = context.bot.send_message.await_args
    assert post is not None
    assert post.kwargs["chat_id"] == 555
    assert post.kwargs["message_thread_id"] == 7
    assert post.kwargs["parse_mode"] == "HTML"
    assert "Buyer Name" in post.kwargs["text"]
    assert "<b>S</b>" in post.kwargs["text"]
    assert "(romaji)" in post.kwargs["text"]
    first.answer.assert_awaited_once_with("Shared with the group.")
    first.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert purchases(session_factory)[0].shared_at is not None

    second = make_query(f"shop:share:{purchase_id}")
    await tap(context, second)

    assert "already shared" in _alert(second)
    assert context.bot.send_message.await_count == 1


async def test_sharing_an_image_clue_reuses_the_file_id(session_factory) -> None:
    _, purchase_id = await _bought(session_factory)
    with session_factory() as session:
        row = session.get(CluePurchase, purchase_id)
        assert row is not None
        row.kind = ClueKind.SCREENSHOT
        row.telegram_file_id = "FILE123"
        session.commit()
    context = make_context(session_factory)
    context.bot.send_photo = AsyncMock()

    await tap(context, make_query(f"shop:share:{purchase_id}"))

    post = context.bot.send_photo.await_args
    assert post is not None
    assert post.kwargs["photo"] == "FILE123"
    assert post.kwargs["chat_id"] == 555
    assert post.kwargs["message_thread_id"] == 7
    assert "Buyer Name" in post.kwargs["caption"]


async def test_image_clue_without_file_id_alerts_failure(session_factory) -> None:
    _, purchase_id = await _bought(session_factory)
    with session_factory() as session:
        row = session.get(CluePurchase, purchase_id)
        assert row is not None
        row.kind = ClueKind.SCREENSHOT
        session.commit()
    context = make_context(session_factory)
    context.bot.send_photo = AsyncMock()
    query = make_query(f"shop:share:{purchase_id}")

    await tap(context, query)

    assert "try again" in _alert(query)
    context.bot.send_photo.assert_not_awaited()
    assert purchases(session_factory)[0].shared_at is None


async def test_share_after_round_ended_is_refused(session_factory) -> None:
    game_id, purchase_id = await _bought(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        game.status = GameStatus.WON
        session.commit()
    context = make_context(session_factory)
    query = make_query(f"shop:share:{purchase_id}")

    await tap(context, query)

    assert "round is over" in _alert(query)
    assert purchases(session_factory)[0].shared_at is None
    assert context.bot.send_message.await_count == 0


async def test_someone_elses_clue_cannot_be_shared(session_factory) -> None:
    _, purchase_id = await _bought(session_factory)
    context = make_context(session_factory)
    query = make_query(f"shop:share:{purchase_id}", user_id=3)

    await tap(context, query)

    assert "isn't your clue" in _alert(query)
    assert purchases(session_factory)[0].shared_at is None
    assert context.bot.send_message.await_count == 0


async def test_unknown_purchase_is_not_yours(session_factory) -> None:
    context = make_context(session_factory)
    query = make_query("shop:share:9999")

    await tap(context, query)

    assert "isn't your clue" in _alert(query)


async def test_malformed_purchase_id_is_answered_silently(session_factory) -> None:
    context = make_context(session_factory)
    query = make_query("shop:share:abc")

    await tap(context, query)

    query.answer.assert_awaited_once_with()
    assert context.bot.send_message.await_count == 0


async def test_failed_share_post_can_be_retried(session_factory) -> None:
    _, purchase_id = await _bought(session_factory)
    context = make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=TimedOut())
    query = make_query(f"shop:share:{purchase_id}")

    await tap(context, query)

    assert purchases(session_factory)[0].shared_at is None
    assert "try again" in _alert(query)

    retry_context = make_context(session_factory)
    retry = make_query(f"shop:share:{purchase_id}")
    await tap(retry_context, retry)

    retry.answer.assert_awaited_once_with("Shared with the group.")
    assert purchases(session_factory)[0].shared_at is not None


async def test_shared_image_caption_is_sent_as_html(session_factory) -> None:
    _, purchase_id = await _bought(session_factory)
    with session_factory() as session:
        row = session.get(CluePurchase, purchase_id)
        assert row is not None
        row.kind = ClueKind.SCREENSHOT
        row.telegram_file_id = "FILE123"
        session.commit()
    context = make_context(session_factory)
    context.bot.send_photo = AsyncMock()
    query = make_query(f"shop:share:{purchase_id}")
    query.from_user.full_name = "A&B"

    await tap(context, query)

    post = context.bot.send_photo.await_args
    assert post is not None
    assert post.kwargs["parse_mode"] == "HTML"
    assert "A&amp;B" in post.kwargs["caption"]
