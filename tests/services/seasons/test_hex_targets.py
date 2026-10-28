from datetime import timedelta

from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.seasons.hex_targets import hex_targets
from tests.hex_fixtures import add_hex
from tests.services.seasons.conftest import NOW, make_game, naive


def _ids(session, season, caster=1) -> list[int]:
    return [p.telegram_user_id for p in hex_targets(session, season, caster, NOW)]


def test_hex_targets_are_eligible_players_only(session, hex_season) -> None:
    # 1 and 2 guessed, 4 hosted; 3 never played; 5 opted out; 6 is hexed
    session.add_all([Player(telegram_user_id=5, hex_opt_out=True), Player(telegram_user_id=6)])
    session.flush()
    game = make_game(session, hex_season.id, 4)
    session.add_all([GameGuess(game_id=game.id, player_id=u, text="x", stage=1) for u in (5, 6)])
    session.flush()
    add_hex(session, 6, HexKind.KAPPA)

    assert sorted(_ids(session, hex_season)) == [2, 4]


def test_hex_targets_sorted_by_activity(session, hex_season) -> None:
    # The fixture's guesses predate the season's start, so they don't count;
    # 4 hosts the fixture's game and this one: 2 x 3 = 6 points at NOW.
    for row in session.query(GameGuess):
        row.created_at = naive(hex_season.start_at) - timedelta(hours=1)
    session.add_all([Player(telegram_user_id=u) for u in (5, 6)])
    session.flush()
    game = make_game(session, hex_season.id, 4)
    late, early = naive(NOW) + timedelta(hours=1), naive(NOW) - timedelta(hours=1)
    session.add_all(
        GameGuess(game_id=game.id, player_id=player, text="x", stage=1, created_at=at)
        for player, at, count in ((2, late, 6), (5, early, 7), (6, late, 7))
        for _ in range(count)
    )
    session.flush()

    # 6 and 5 tie at 7 (6 is more recent); 2 and 4 tie at 6 (2 is more recent)
    assert _ids(session, hex_season) == [6, 5, 2, 4]


def test_hex_targets_leave_out_the_caster(session, hex_season) -> None:
    assert 2 not in _ids(session, hex_season, caster=2)
