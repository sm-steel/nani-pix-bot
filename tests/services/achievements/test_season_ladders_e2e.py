from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    GameStatus,
    ItemSource,
    PixelStage,
    SeasonStatus,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import catalogue
from nani_pix_bot.services.cosmetics.inventory import ItemGrant, grant
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from nani_pix_bot.services.game import state
from nani_pix_bot.services.search import tags
from nani_pix_bot.services.seasons import season_pass
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements

STATIC = catalogue.CATALOGUE
LADDER_KEYS = (
    "season_veteran",
    "season_completionist",
    "season_front_runner",
    "season_hoarder",
    "season_patron",
    "season_genre_explorer",
)


@pytest.fixture(autouse=True)
def _season_runs(fake_runs, monkeypatch):
    """The shared fake runs; only the lifetime season ladders evaluate, since
    the minimal events these fixtures emit lack fields the other static ones read."""
    only = tuple(d for d in STATIC if d.key in LADDER_KEYS)
    monkeypatch.setattr(catalogue, "CATALOGUE", only)


@pytest.fixture
def shop_open(session, active_season) -> None:
    """Game-less events are stamped with the real clock, so the season's window
    has to surround it."""
    now = datetime.now(UTC)
    active_season.start_at = now - timedelta(days=1)
    active_season.end_at = now + timedelta(days=9)
    session.flush()


def _grants(session, key: str) -> list[tuple[int, int]]:
    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.key == key))
    return [(r.player_id, r.tier) for r in rows]


def _complete_free_pass(session, season, player: int) -> None:
    session.add(SeasonXp(season_id=season.id, player_id=player, amount=120, source="win"))
    session.flush()
    run = registry.get("demo_1")
    assert run is not None
    season_pass.sync(session, season, run, player)


def test_the_first_to_complete_the_pass_is_a_front_runner(
    session, active_season, tagged_game
) -> None:
    _complete_free_pass(session, active_season, WINNER)
    _complete_free_pass(session, active_season, STARTER)
    assert _grants(session, "season_front_runner") == [(WINNER, 1)]
    assert sorted(_grants(session, "season_completionist")) == [(WINNER, 1), (STARTER, 1)]


def test_buying_premium_earns_the_patron(
    session, active_season, tagged_game, shop_open, buyer
) -> None:
    wallet.credit(session, buyer, 500, LedgerEntry(CurrencyReason.GRANT))
    run = registry.get("demo_1")
    assert run is not None
    season_pass.buy_premium(session, active_season, run, buyer)
    assert _grants(session, "season_patron") == [(WINNER, 1)]


def test_five_distinct_cosmetics_earn_the_hoarder(session, tagged_game, shop_open, buyer) -> None:
    items = [
        "name_flair.blossom",
        "guess_reaction.love",
        "host_frame.border",
        "host_transition.iris",
        "win_effect.confetti",
    ]
    for number, key in enumerate(items, start=1):
        grant(session, WINNER, ItemGrant(f"demo_1/{key}", ItemSource.ADMIN))
        expected = [] if number < len(items) else [(WINNER, 1)]
        assert _grants(session, "season_hoarder") == expected


def test_winning_in_two_gated_genres_earns_the_explorer(session, tagged_game) -> None:
    def win(tag: str) -> None:
        events.emit(
            session,
            EventType.GAME_WON,
            events.Involved(actor_id=WINNER, subject_id=STARTER, game_id=tagged_game.id),
            stage=2,
            hard_mode=False,
            gate_tags=[tag],
        )

    win("Romance")
    assert _grants(session, "season_genre_explorer") == []
    win("Action")
    assert _grants(session, "season_genre_explorer") == [(WINNER, 1)]


def _demo_2_game(session) -> Game:
    season = SeasonSchedule(
        run_id="demo_2",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.flush()
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_2,
        title_english="Naruto",
        season_id=season.id,
    )
    session.add(game)
    session.flush()
    return game


def test_real_wins_in_two_gated_genres_earn_the_explorer(session, tagged_game) -> None:
    """Through state's real win, which reads the gate tags into win_facts."""
    tagged_game.anime_tags = tags.to_json([tags.AnimeTag("genre", "Romance", 22)])
    session.flush()
    state.force_win(session, tagged_game, winner_id=WINNER)
    assert _grants(session, "season_genre_explorer") == []

    action_game = _demo_2_game(session)
    action_game.anime_tags = tags.to_json([tags.AnimeTag("genre", "Action", 1)])
    session.flush()
    state.force_win(session, action_game, winner_id=WINNER)
    assert _grants(session, "season_genre_explorer") == [(WINNER, 1)]
