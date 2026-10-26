from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import announcements
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import GameStatus, OutboxKind, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cards import GoalCard, backgrounds
from tests.jobs.test_announcements import _context, _drain


@pytest.fixture(autouse=True)
def _runs(fake_runs):
    """The shared fake runs."""


@pytest.fixture(autouse=True)
def _no_avatars(monkeypatch) -> None:
    monkeypatch.setattr(announcements, "fetch_avatar", AsyncMock(return_value=None))


def _row(session, kind, season, **payload) -> AnnouncementOutbox:
    row = AnnouncementOutbox(kind=kind, payload={"season_id": season.id, **payload})
    session.add(row)
    session.flush()
    return row


def _render(session, row, lang="EN"):
    return announcements.RENDERERS[OutboxKind(row.kind)](session, row, lang)


def _season(session, run_id="demo_3") -> SeasonSchedule:
    season = SeasonSchedule(
        run_id=run_id,
        start_at=datetime(2026, 10, 26, tzinfo=UTC),
        end_at=datetime(2026, 11, 9, tzinfo=UTC),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.flush()
    return season


def test_milestone_post_has_card_and_text(session, yokai_season):
    row = _row(session, OutboxKind.GOAL_MILESTONE, yokai_season, milestone=5)
    post = _render(session, row)
    assert post.card == GoalCard("demo_3", 5, 10)
    assert post.text.startswith("🕯 5 of 10 candles are out")


@pytest.mark.parametrize(
    ("lang", "players", "expected"),
    [("RU", 3, "3 игрока"), ("RU", 5, "5 игроков"), ("RU", 1, "1 игрок"), ("EN", 1, "1 player ")],
)
def test_reached_post_names_players_and_reward(session, yokai_season, lang, players, expected):
    row = _row(session, OutboxKind.GOAL_REACHED, yokai_season, players=players, candles=10)
    post = _render(session, row, lang)
    assert expected in post.text
    assert post.card == GoalCard("demo_3", 10, 10)
    assert "20 🪙" in post.text
    assert ("Свеча" if lang == "RU" else "Candle") in post.text


def _boss(session, yokai_season, *, boss_game=True) -> AnnouncementOutbox:
    session.add(Player(telegram_user_id=2))
    game = Game(
        starter_id=2, status=GameStatus.ACTIVE, season_id=yokai_season.id, boss_game=boss_game
    )
    session.add(game)
    session.flush()
    return _row(session, OutboxKind.GOAL_BOSS, yokai_season, game_id=game.id)


def test_boss_post_has_the_boss_card_and_multipliers(session, yokai_season):
    row = _boss(session, yokai_season)
    post = _render(session, row)
    assert post.card == GoalCard("demo_3", 10, 10, boss=True)
    assert f"Game #{row.payload['game_id']}" in post.text
    game_id = row.payload["game_id"]
    assert post.text == i18n.t("goal.post.boss", "en", game_id=game_id, xp=2, coins=3)


def test_boss_post_skipped_when_the_game_was_stopped(session, yokai_season, log_records):
    row = _boss(session, yokai_season)
    session.delete(session.get(Game, row.payload["game_id"]))
    session.flush()
    assert _render(session, row) is None
    assert any(line.level == "INFO" and "boss" in line.message for line in log_records)


def test_boss_post_skipped_when_the_game_is_no_longer_the_boss(session, yokai_season):
    assert _render(session, _boss(session, yokai_season, boss_game=False)) is None


def test_bone_dm_goes_to_the_player(session, yokai_season):
    row = _row(session, OutboxKind.BONE_FOUND, yokai_season, player_id=7, bones=2, granted=False)
    post = _render(session, row)
    assert post.dm_to == 7
    assert post.card is None
    assert "2/3" in post.text


def test_bone_dm_names_the_item_when_granted(session, yokai_season):
    row = _row(session, OutboxKind.BONE_FOUND, yokai_season, player_id=7, bones=3, granted=True)
    text = _render(session, row).text
    assert "Bone" in text
    assert "3/3" in text


@pytest.mark.parametrize(
    "kind", [OutboxKind.GOAL_MILESTONE, OutboxKind.GOAL_REACHED, OutboxKind.BONE_FOUND]
)
def test_a_cancelled_season_posts_nothing(session, yokai_season, kind):
    yokai_season.status = SeasonStatus.CANCELLED
    payload = {"milestone": 5, "players": 1, "player_id": 7, "bones": 1, "granted": False}
    assert _render(session, _row(session, kind, yokai_season, **payload)) is None


def test_a_run_without_a_goal_posts_nothing(session):
    season = _season(session, "demo_1")
    assert _render(session, _row(session, OutboxKind.GOAL_MILESTONE, season, milestone=5)) is None


async def test_goal_posts_fall_back_to_text_without_art(session_factory, monkeypatch, tmp_path):
    monkeypatch.setattr(backgrounds, "SEASON_ART_DIR", tmp_path)
    with session_scope(session_factory) as session:
        _row(session, OutboxKind.GOAL_MILESTONE, _season(session), milestone=5)
    context = _context(session_factory)
    await _drain(context)
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_awaited_once()
    assert "5 of 10 candles" in context.bot.send_message.await_args.kwargs["text"]


async def test_goal_post_with_art_goes_out_as_a_photo(session_factory, monkeypatch, tmp_path):
    folder = tmp_path / "demo_3" / "goal"
    folder.mkdir(parents=True)
    for name in ("background", "skulls_0", "skulls_1", "skulls_2", "skulls_3", "skulls_4"):
        Image.new("RGBA", (1200, 630), (0, 0, 0, 255)).save(folder / f"{name}.png")
    for name, size in (
        ("gashadokuro", (80, 83)),
        ("candle_lit", (35, 160)),
        ("candle_out", (35, 160)),
    ):
        Image.new("RGBA", size, (255, 255, 255, 255)).save(folder / f"{name}.png")
    monkeypatch.setattr(backgrounds, "SEASON_ART_DIR", tmp_path)
    with session_scope(session_factory) as session:
        _row(session, OutboxKind.GOAL_MILESTONE, _season(session), milestone=5)
    context = _context(session_factory)
    await _drain(context)
    context.bot.send_photo.assert_awaited_once()
