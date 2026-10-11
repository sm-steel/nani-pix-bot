"""Every guesser is evaluated when a game ends (#405): a guesser who neither
won nor hosted earns what the game earned them (Sakura's Hanami Party), and
nothing the winner or the host earned. The real registry, the full catalogue
and the real game services."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events
from nani_pix_bot.services import game as game_service
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements

BYSTANDER = 3
OTHERS = (4, 5)
PARTY = "sakura_1.hanami_party"


@pytest.fixture(autouse=True)
def _real_runs():
    registry.all_runs.cache_clear()
    registry.all_items.cache_clear()
    yield
    registry.all_items.cache_clear()


@pytest.fixture
def party_game(session) -> Game:
    season = SeasonSchedule(
        run_id="sakura_1",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.add_all([Player(telegram_user_id=u) for u in (WINNER, STARTER, BYSTANDER, *OTHERS)])
    session.flush()
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_2,
        title_english="Clannad",
        season_id=season.id,
    )
    session.add(game)
    session.flush()
    for player in (BYSTANDER, *OTHERS, WINNER):
        session.add(GameGuess(game_id=game.id, player_id=player, text="x", stage=2))
        session.flush()
        events.emit(session, EventType.GUESS, events.Involved(actor_id=player, game_id=game.id))
    return game


def _keys(session, player: int) -> set[str]:
    stmt = select(AchievementGrant.key).where(AchievementGrant.player_id == player)
    return set(session.scalars(stmt))


def test_a_bystander_guesser_gets_hanami_party_when_a_four_guesser_game_is_won(
    session, party_game
) -> None:
    before = _keys(session, BYSTANDER)

    game_service.force_win(session, party_game, winner_id=WINNER)

    assert _keys(session, BYSTANDER) - before == {PARTY}


def test_a_bystander_guesser_gets_hanami_party_when_it_ends_unsolved(session, party_game) -> None:
    before = _keys(session, BYSTANDER)

    game_service.force_unsolved(party_game, cause="timeout")

    assert _keys(session, BYSTANDER) - before == {PARTY}


def test_the_bystander_gets_nothing_the_winner_or_the_host_got(session, party_game) -> None:
    before = {p: _keys(session, p) for p in (WINNER, STARTER, BYSTANDER)}

    game_service.force_win(session, party_game, winner_id=WINNER)

    gained = {p: _keys(session, p) - before[p] for p in before}
    # The event did grant winner- and host-only achievements...
    assert {"sharpshooter", "pioneer"} <= gained[WINNER]
    assert "storyteller" in gained[STARTER]
    # ...and the bystander got only the party, none of those.
    assert gained[BYSTANDER] == {PARTY}
