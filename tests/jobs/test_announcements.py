from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session, sessionmaker
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import announcements
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import OutboxKind, Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import engine, outbox
from nani_pix_bot.services.quiet_hours import QuietHours
from tests.conftest import LogLine

pytestmark = pytest.mark.achievements


@pytest.fixture(autouse=True)
def _no_avatars(monkeypatch) -> None:
    monkeypatch.setattr(announcements, "fetch_avatar", AsyncMock(return_value=None))


def _context(session_factory: sessionmaker[Session]) -> MagicMock:
    context = MagicMock()
    context.job = None
    context.bot.send_message = AsyncMock()
    context.bot.send_photo = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


def _grant(session_factory: sessionmaker[Session], key: str = "kingmaker") -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        engine.grant(session, engine.GrantRequest(1, key))


async def _drain(context: MagicMock) -> None:
    await announcements.drain_outbox(cast(ContextTypes.DEFAULT_TYPE, context))


async def test_an_unlock_is_posted_once_into_the_game_topic(session_factory) -> None:
    _grant(session_factory)
    context = _context(session_factory)

    await _drain(context)
    await _drain(context)

    context.bot.send_photo.assert_awaited_once()
    kwargs = context.bot.send_photo.await_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_thread_id"]) == (555, 7)
    assert "@alice" in kwargs["caption"]
    assert "Kingmaker" in kwargs["caption"]
    assert kwargs["photo"][:4] == b"\x89PNG"


async def test_cascaded_unlocks_post_in_grant_order(session_factory) -> None:
    _grant(session_factory, "pioneer")
    context = _context(session_factory)

    await _drain(context)

    texts = [c.kwargs["caption"] for c in context.bot.send_photo.await_args_list]
    assert "Pioneer" in texts[0]
    assert "Pixel Magnate" in texts[1]


async def test_nothing_is_posted_during_quiet_hours(session_factory, quiet_now: QuietHours) -> None:
    _grant(session_factory)
    with session_scope(session_factory) as session:
        settings.set_quiet_hours(session, quiet_now)
    context = _context(session_factory)

    await _drain(context)

    context.bot.send_message.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    with session_scope(session_factory) as session:
        assert len(outbox.pending(session, 10)) == 1


async def test_a_failing_post_gives_up_after_three_attempts(session_factory, records) -> None:
    _grant(session_factory)
    context = _context(session_factory)
    context.bot.send_photo.side_effect = TelegramError("boom")

    for _ in range(outbox.MAX_ATTEMPTS + 1):
        await _drain(context)

    assert context.bot.send_photo.await_count == outbox.MAX_ATTEMPTS
    assert any(level == "ERROR" and "giving up" in message for level, message in records)


async def test_an_unrenderable_row_does_not_block_the_queue(session_factory, records) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        bad = AchievementGrant(
            player_id=1, key="no_such_achievement", tier=1, rarity=Rarity.BRONZE, reward=0, points=1
        )
        session.add(bad)
        session.flush()
        outbox.enqueue_unlock(session, bad.id, None)
        engine.grant(session, engine.GrantRequest(1, "kingmaker"))
    context = _context(session_factory)

    for _ in range(outbox.MAX_ATTEMPTS):
        await _drain(context)

    context.bot.send_photo.assert_awaited_once()
    assert "Kingmaker" in context.bot.send_photo.await_args.kwargs["caption"]
    with session_scope(session_factory) as session:
        assert outbox.pending(session, 10) == []
    assert any(level == "ERROR" and "could not be rendered" in m for level, m in records)


async def test_a_failed_send_stops_the_drain_and_spares_later_rows(session_factory) -> None:
    _grant(session_factory, "pioneer")  # queues pioneer, then pixel_magnate
    context = _context(session_factory)
    context.bot.send_photo.side_effect = TelegramError("boom")

    await _drain(context)

    context.bot.send_photo.assert_awaited_once()
    with session_scope(session_factory) as session:
        first, second = outbox.pending(session, 10)
        assert (first.attempts, second.attempts) == (1, 0)


async def test_a_period_summary_lists_the_podium(session_factory) -> None:
    from nani_pix_bot.models.enums import PeriodType
    from nani_pix_bot.models.period import PeriodResult

    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=1, username="alice"), Player(telegram_user_id=2)])
        session.flush()
        session.add_all(
            [
                PeriodResult(
                    period_type=PeriodType.MONTH,
                    period_key="2026-10",
                    rank=1,
                    player_id=1,
                    score=14,
                    wins=3,
                ),
                PeriodResult(
                    period_type=PeriodType.MONTH,
                    period_key="2026-10",
                    rank=2,
                    player_id=2,
                    score=9,
                    wins=2,
                ),
            ]
        )
        outbox.enqueue_period_summary(session, "month", "2026-10")
    context = _context(session_factory)

    await _drain(context)

    text = context.bot.send_photo.await_args.kwargs["caption"]
    assert "October 2026" in text
    assert "🥇 @alice — 14 🌟 · 3 👑" in text
    assert "🥈 2 — 9 🌟 · 2 👑" in text
    assert text.startswith("🌟 ")


async def test_a_shared_first_place_posts_two_gold_medals(session_factory) -> None:
    from nani_pix_bot.models.enums import PeriodType
    from nani_pix_bot.models.period import PeriodResult

    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=i, username=f"p{i}") for i in (1, 2, 3)])
        session.flush()
        session.add_all(
            PeriodResult(
                period_type=PeriodType.WEEK,
                period_key="2026-W41",
                rank=rank,
                player_id=player,
                score=score,
                wins=1,
            )
            for player, rank, score in ((3, 3, 2), (1, 1, 6), (2, 1, 6))
        )
        outbox.enqueue_period_summary(session, "week", "2026-W41")
    context = _context(session_factory)

    await _drain(context)

    lines = context.bot.send_photo.await_args.kwargs["caption"].splitlines()
    assert lines[1:] == [
        "🥇 @p1 — 6 🌟 · 1 👑",
        "🥇 @p2 — 6 🌟 · 1 👑",
        "🥉 @p3 — 2 🌟 · 1 👑",
    ]


async def test_three_unlocks_from_one_event_post_as_one_album(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        for key in ("kingmaker", "clutch", "first_try"):
            engine.grant(session, engine.GrantRequest(1, key), batch_id=99)
    context = _context(session_factory)

    await _drain(context)

    context.bot.send_media_group.assert_awaited_once()
    media = context.bot.send_media_group.await_args.kwargs["media"]
    assert len(media) == 3
    for item, title in zip(media, ("Kingmaker", "Clutch", "First Try"), strict=True):
        assert f"«{title}»" in item.caption
    context.bot.send_photo.assert_not_awaited()


async def test_a_card_that_fails_to_render_posts_as_text(
    session_factory, monkeypatch, records
) -> None:
    _grant(session_factory)
    monkeypatch.setattr(
        announcements, "render_unlock_card", MagicMock(side_effect=ValueError("bad"))
    )
    context = _context(session_factory)

    await _drain(context)

    context.bot.send_photo.assert_not_awaited()
    assert "Kingmaker" in context.bot.send_message.await_args.kwargs["text"]
    assert any(level == "ERROR" for level, _ in records)


def test_card_text_drops_the_currency_emoji_with_its_leading_space() -> None:
    assert (
        announcements._card_text("Потрачено 💠 на подсказки: 10000")
        == "Потрачено на подсказки: 10000"
    )
    assert announcements._card_text("Spent 💠 on clues, 💠 total") == "Spent on clues, total"


async def test_backgrounds_are_loaded_per_slot_seeded_by_the_player(
    session_factory, monkeypatch
) -> None:
    _grant(session_factory)
    loader = MagicMock(return_value=None)
    monkeypatch.setattr(announcements, "load_background", loader)
    context = _context(session_factory)

    await _drain(context)

    assert loader.call_args.args == ("unlock/bronze", 1)


def _post(row_id: int, batch_id: int | None) -> announcements.Post:
    return announcements.Post((row_id,), f"post {row_id}", batch_id=batch_id)


def _shape(batches: list[list[announcements.Post]]) -> list[list[int]]:
    return [[i for post in batch for i in post.row_ids] for batch in batches]


def test_batches_group_consecutive_posts_of_the_same_event() -> None:
    posts = [_post(1, 7), _post(2, 7), _post(3, 7)]
    assert _shape(announcements._batches(posts)) == [[1, 2, 3]]


def test_batches_under_the_album_minimum_go_out_singly() -> None:
    posts = [_post(1, 7), _post(2, 7)]
    assert _shape(announcements._batches(posts)) == [[1], [2]]


def test_batches_never_group_posts_without_a_batch_id() -> None:
    posts = [_post(1, None), _post(2, None), _post(3, None)]
    assert _shape(announcements._batches(posts)) == [[1], [2], [3]]


def test_batches_split_eleven_into_an_album_of_ten_and_a_single() -> None:
    posts = [_post(i, 7) for i in range(1, 12)]
    assert _shape(announcements._batches(posts)) == [list(range(1, 11)), [11]]


def test_batches_keep_id_order_across_singles_and_albums() -> None:
    posts = [
        _post(1, None),
        _post(2, 7),
        _post(3, 7),
        _post(4, 7),
        _post(5, 8),
        _post(6, None),
        _post(7, 9),
        _post(8, 9),
        _post(9, 9),
    ]
    assert _shape(announcements._batches(posts)) == [[1], [2, 3, 4], [5], [6], [7, 8, 9]]


def _album_then_single(session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=1, username="alice"), Player(telegram_user_id=2)])
        session.flush()
        for key in ("kingmaker", "clutch", "first_try"):
            engine.grant(session, engine.GrantRequest(1, key), batch_id=99)
        engine.grant(session, engine.GrantRequest(2, "kingmaker"))


async def test_a_failed_album_fails_every_row_and_spares_later_rows(session_factory) -> None:
    _album_then_single(session_factory)
    context = _context(session_factory)
    context.bot.send_media_group.side_effect = TelegramError("boom")

    await _drain(context)

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    with session_scope(session_factory) as session:
        attempts = [row.attempts for row in outbox.pending(session, 10)]
    assert attempts == [1, 1, 1, 0]


async def test_an_album_with_one_undrawable_card_reuses_what_was_drawn(
    session_factory, monkeypatch
) -> None:
    _album_then_single(session_factory)
    real = announcements.render_unlock_card
    calls = []

    def flaky(card, avatar, background=None):
        calls.append(card.name)
        if card.name == "Clutch":
            raise ValueError("bad")
        return real(card, avatar, background)

    monkeypatch.setattr(announcements, "render_unlock_card", flaky)
    context = _context(session_factory)

    await _drain(context)

    assert calls == ["Kingmaker", "Clutch", "First Try", "Kingmaker"]  # nothing drawn twice
    context.bot.send_media_group.assert_not_awaited()
    captions = [c.kwargs["caption"] for c in context.bot.send_photo.await_args_list]
    assert ["«Kingmaker»" in captions[0], "«First Try»" in captions[1]] == [True, True]
    assert len(captions) == 3  # the two good album cards and the later single
    assert "«Clutch»" in context.bot.send_message.await_args.kwargs["text"]
    with session_scope(session_factory) as session:
        assert outbox.pending(session, 10) == []


async def test_a_failure_midway_through_the_fallback_keeps_what_was_posted(
    session_factory, monkeypatch
) -> None:
    _album_then_single(session_factory)
    draw = MagicMock(side_effect=[b"a", ValueError("bad"), b"c", b"d"])
    monkeypatch.setattr(announcements, "_draw", draw)
    context = _context(session_factory)
    context.bot.send_photo.side_effect = [None, TelegramError("boom")]

    await _drain(context)

    context.bot.send_message.assert_awaited_once()  # the undrawable card, as text
    assert context.bot.send_photo.await_count == 2
    with session_scope(session_factory) as session:
        remaining = [(row.id, row.attempts) for row in outbox.pending(session, 10)]
    assert remaining == [(3, 1), (4, 0)]  # first two were posted; the third failed


async def test_an_avatar_is_fetched_once_per_player_per_drain(session_factory, monkeypatch) -> None:
    _album_then_single(session_factory)
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(announcements, "fetch_avatar", fetch)
    context = _context(session_factory)

    await _drain(context)

    assert sorted(call.args[1] for call in fetch.await_args_list) == [1, 2]


async def test_a_renderer_crash_fails_that_row_and_posts_the_next(
    session_factory, monkeypatch, log_records: list[LogLine]
) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1, username="alice"))
        session.flush()
        first = engine.grant(session, engine.GrantRequest(1, "clutch"))
        engine.grant(session, engine.GrantRequest(1, "kingmaker"))
        broken_grant = first.id
    real = announcements.RENDERERS[OutboxKind.UNLOCK]

    def crashing(session: Session, row, lang: str):
        if row.payload["grant_id"] == broken_grant:
            msg = "card template broke"
            raise RuntimeError(msg)
        return real(session, row, lang)

    monkeypatch.setitem(announcements.RENDERERS, OutboxKind.UNLOCK, crashing)
    context = _context(session_factory)

    await _drain(context)

    context.bot.send_photo.assert_awaited_once()
    assert "Kingmaker" in context.bot.send_photo.await_args.kwargs["caption"]
    with session_scope(session_factory) as session:
        (left,) = outbox.pending(session, 10)
        assert (left.payload["grant_id"], left.attempts) == (broken_grant, 1)
    (error,) = [r for r in log_records if r.level == "ERROR"]
    assert "could not be rendered" in error.message
    assert "RuntimeError" in error.message
