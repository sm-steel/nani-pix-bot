from datetime import UTC, datetime, timedelta

from nani_pix_bot.models.enums import GameStatus, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import game as game_service


def _setup_game(session) -> Game:
    session.add(Player(telegram_user_id=1))
    game = Game(starter_id=1, status=GameStatus.SETUP, title_english="Clannad", source="manual")
    session.add(game)
    session.flush()
    return game


def test_activation_tags_the_game_with_the_active_season(session) -> None:
    now = datetime.now(UTC)
    season = SeasonSchedule(
        run_id="demo_1",
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    game = _setup_game(session)
    game_service.activate_game(session, game)
    assert game.season_id == season.id


def test_activation_without_a_season_leaves_it_untagged(session) -> None:
    game = _setup_game(session)
    game_service.activate_game(session, game)
    assert game.season_id is None
