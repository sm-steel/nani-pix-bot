from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import candles
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus, SeasonStatus
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import GroupGoal, HexKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons import group_goal

HAUNT = "kitsune_mirror"
VIEWER = 5


@pytest.fixture(autouse=True)
def _runs(fake_runs):
    """The shared fake runs."""


def _found(session, season):
    found = group_goal.goal_of(session, season.id)
    assert found is not None
    return found


def _text(session, season, viewer: int = VIEWER, lang: str = "EN") -> str:
    found = _found(session, season)
    snuffed = group_goal.candles(session, season.id, found[2])
    return candles.candles_text(session, found, (snuffed, viewer), lang)


def _settle(session, season) -> None:
    assert group_goal.settle(session, season, _found(session, season)[1], None)


def test_candles_text_before_the_goal(session, yokai_season, finished_game):
    # 1 + 2 + 3 + 1 candles, the viewer guessed in the first game
    finished_game(GameStatus.WON, guessers=(VIEWER,))
    finished_game(GameStatus.UNSOLVED, haunt_twist=HAUNT)
    finished_game(GameStatus.WON, event_day=True)
    finished_game(GameStatus.WON)
    lines = _text(session, yokai_season).splitlines()
    assert lines == [
        "🕯 100 Candles · Third Demo",
        "7 of 10 candles snuffed",
        "A finished game snuffs 1, a haunted round 2, Halloween snuffs 3.",
        "You helped put out the candles ✅",
        "🦴 Your bones: 0/3",
    ]


def test_candles_text_asks_a_newcomer_to_help(session, yokai_season, finished_game):
    finished_game(GameStatus.WON)
    assert "Guess in or host a season game to help." in _text(session, yokai_season)


def test_next_milestone_is_the_next_multiple_below_the_target():
    goal = GroupGoal(target=100)
    assert candles._next_milestone(0, goal) == 25
    assert candles._next_milestone(30, goal) == 50
    assert candles._next_milestone(75, goal) is None
    assert candles._next_milestone(10, GroupGoal(target=100, milestone_every=0)) is None


def test_candles_text_after_reaching_shows_boss_pending_and_you_helped(
    session, yokai_season, finished_game
):
    finished_game(GameStatus.WON, guessers=(VIEWER,), haunt_twist=HAUNT, event_day=True)
    for _ in range(3):
        finished_game(GameStatus.WON, haunt_twist=HAUNT, event_day=True)
    _settle(session, yokai_season)
    text = _text(session, yokai_season)
    assert "12 of 10 candles snuffed" in text
    assert "The Gashadokuro is awake: Nani Pix is about to start the boss game!" in text
    assert "You helped put out the candles ✅" in text
    assert "Next milestone" not in text
    assert "Guess in or host" not in text


def test_candles_text_boss_won_names_the_winner(session, yokai_season, finished_game):
    for _ in range(4):
        finished_game(GameStatus.WON, haunt_twist=HAUNT, event_day=True)
    _settle(session, yokai_season)
    boss = finished_game(GameStatus.WON, boss_game=True, winner_id=7)
    player = session.get(Player, 7)
    assert player is not None
    player.username = "kurogane"
    session.flush()
    text = _text(session, yokai_season)
    assert f"Game #{boss.id} defeated the Gashadokuro: @kurogane landed the final blow." in text


def test_candles_text_boss_running_then_unsolved(session, yokai_season, finished_game):
    for _ in range(4):
        finished_game(GameStatus.WON, haunt_twist=HAUNT, event_day=True)
    _settle(session, yokai_season)
    boss = finished_game(GameStatus.ACTIVE, boss_game=True)
    assert f"The Gashadokuro boss game #{boss.id} is on!" in _text(session, yokai_season)
    boss.status = GameStatus.UNSOLVED
    session.flush()
    assert f"The Gashadokuro survived game #{boss.id}." in _text(session, yokai_season)


def test_candles_text_bones_line(session, yokai_season, finished_game):
    for _ in range(3):
        finished_game(GameStatus.WON, haunt_twist=HAUNT, winner_id=VIEWER)
    assert "🦴 Your bones: 3/3" in _text(session, yokai_season)


def test_candles_text_in_russian_uses_plural_forms(session, yokai_season, finished_game):
    finished_game(GameStatus.WON)
    assert "Погашена 1 свеча из 10" in _text(session, yokai_season, lang="RU")
    finished_game(GameStatus.WON)
    assert "Погашено 2 свечи из 10" in _text(session, yokai_season, lang="RU")


def _update(thread_id: int | None = 7, chat_type: str = "supergroup") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = VIEWER
    update.effective_chat.id = 555
    update.effective_chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.message.chat_id = 555
    update.effective_message = update.message
    update.message.reply_photo = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


def _season(session_factory, run_id: str) -> None:
    now = datetime.now(UTC)
    with session_scope(session_factory) as session:
        session.add(
            SeasonSchedule(
                run_id=run_id,
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=9),
                status=SeasonStatus.ACTIVE,
                created_by=9,
            )
        )


async def _run(update: MagicMock, session_factory) -> None:
    context = _context(session_factory)
    await candles.candles_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))


async def test_candles_replies_photo_in_topic(session_factory, log_records):
    _season(session_factory, "demo_3")
    update = _update()
    with patch.object(candles, "render_goal_card", return_value=b"png") as render:
        await _run(update, session_factory)
    update.message.reply_photo.assert_awaited_once()
    kwargs = update.message.reply_photo.await_args.kwargs
    assert kwargs["photo"] == b"png"
    assert "0 of 10 candles snuffed" in kwargs["caption"]
    assert render.call_args.args[0].target == 10
    line = next(r for r in log_records if r.message == "viewed /candles")
    assert (line.level, line.extra["snuffed"], line.extra["target"]) == ("INFO", 0, 10)


async def test_candles_without_art_replies_text(session_factory):
    _season(session_factory, "demo_3")
    update = _update()
    with patch.object(candles, "render_goal_card", side_effect=FileNotFoundError("art")):
        await _run(update, session_factory)
    update.message.reply_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    assert "candles snuffed" in update.message.reply_text.await_args.args[0]


async def test_candles_none_without_group_goal(session_factory):
    _season(session_factory, "demo_1")
    update = _update()
    await _run(update, session_factory)
    update.message.reply_text.assert_awaited_once_with(i18n.t("candles.none", "en"))
    update.message.reply_photo.assert_not_awaited()


async def test_candles_works_in_a_dm(session_factory):
    _season(session_factory, "demo_3")
    update = _update(thread_id=None, chat_type="private")
    with patch.object(candles, "render_goal_card", return_value=b"png"):
        await _run(update, session_factory)
    update.message.reply_photo.assert_awaited_once()


async def test_candles_ignored_outside_topic_and_dm(session_factory):
    _season(session_factory, "demo_3")
    update = _update(thread_id=99)
    await _run(update, session_factory)
    update.message.reply_photo.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


def _settled_boss(session, season, finished_game, **boss_fields):
    for _ in range(4):
        finished_game(GameStatus.WON, haunt_twist=HAUNT, event_day=True)
    _settle(session, season)
    return finished_game(GameStatus.WON, boss_game=True, **boss_fields)


def test_candles_text_boss_won_without_a_winner_does_not_say_survived(
    session, yokai_season, finished_game
):
    boss = _settled_boss(session, yokai_season, finished_game)
    text = _text(session, yokai_season)
    assert f"Game #{boss.id} defeated the Gashadokuro!" in text
    assert "survived" not in text


def test_candles_text_boss_winner_under_nopperabo_is_masked(session, yokai_season, finished_game):
    boss = _settled_boss(session, yokai_season, finished_game, winner_id=7)
    player = session.get(Player, 7)
    assert player is not None
    player.username = "kurogane"
    session.add(
        Hex(
            season_id=yokai_season.id,
            kind=HexKind.NOPPERABO.value,
            caster_id=1,
            target_id=7,
            cast_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
    )
    session.flush()
    text = _text(session, yokai_season)
    assert f"Game #{boss.id} defeated the Gashadokuro: 👤 landed the final blow." in text
    assert "kurogane" not in text
