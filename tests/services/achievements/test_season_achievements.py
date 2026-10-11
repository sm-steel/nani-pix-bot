from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    GameStatus,
    SeasonStatus,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.achievements import catalogue, names
from nani_pix_bot.services.achievements.definitions import grants_title
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from nani_pix_bot.services.seasons import lifecycle, season_pass, season_shop
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements

DANGO, BORDER = 0, 1
STATIC = catalogue.CATALOGUE


@pytest.fixture(autouse=True)
def _season_runs(fake_runs, monkeypatch):
    """The shared fake runs; only the season definitions evaluate, since the
    minimal events these fixtures emit lack fields the static ones read."""
    only_season = tuple(d for d in STATIC if d.key.startswith("season_"))
    monkeypatch.setattr(catalogue, "CATALOGUE", only_season)


def _grants(session, key: str) -> list[tuple[int, int, str]]:
    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.key == key))
    return [(r.player_id, r.tier, r.period_key) for r in rows]


def _run():
    run = registry.get("demo_1")
    assert run is not None
    return run


@pytest.fixture
def shop_open(session, active_season) -> None:
    """Game-less events (a shop charge) are stamped with the real clock, so the
    season's window has to surround it."""
    now = datetime.now(UTC)
    active_season.start_at = now - timedelta(days=1)
    active_season.end_at = now + timedelta(days=9)
    session.flush()


def _credit_coins(session, season, amount: int) -> None:
    entry = LedgerEntry(CurrencyReason.SEASON_WIN, season_id=season.id, coins=True)
    wallet.credit(session, session.get(Player, WINNER), amount, entry)


def test_playing_a_season_game_earns_the_debut(session, tagged_game, emit_guess) -> None:
    emit_guess(player=WINNER)
    assert _grants(session, "season_debut") == [(WINNER, 1, "demo_1")]


def test_a_pass_completed_while_closing_earns_pass_complete(
    session, active_season, tagged_game
) -> None:
    session.add(SeasonXp(season_id=active_season.id, player_id=WINNER, amount=120, source="win"))
    active_season.status = SeasonStatus.CLOSING
    active_season.end_at = NOW - timedelta(hours=1)
    session.flush()
    season_pass.sync(session, active_season, _run(), WINNER)
    assert _grants(session, "season_pass_complete") == [(WINNER, 1, "demo_1")]


def _finalize(session, game, season) -> None:
    game.status = GameStatus.WON
    season.status = SeasonStatus.CLOSING
    season.end_at = NOW - timedelta(hours=1)
    session.flush()
    lifecycle.advance(session, NOW)


def test_the_podium_is_granted_at_finalize(session, active_season, tagged_game) -> None:
    for player, amount in ((WINNER, 50), (STARTER, 20)):
        session.add(
            SeasonXp(season_id=active_season.id, player_id=player, amount=amount, source="win")
        )
    session.flush()
    _finalize(session, tagged_game, active_season)
    assert _grants(session, "season_podium_1") == [(WINNER, 1, "demo_1")]
    assert _grants(session, "season_podium_2") == [(STARTER, 1, "demo_1")]
    assert _grants(session, "season_podium_3") == []


def test_the_champion_title_names_the_season(session, active_season, tagged_game) -> None:
    defn = catalogue.get("season_podium_1")
    assert grants_title(defn, 1) is True
    assert names.title(defn, 1, "demo_1", "EN") == "Champion of Demo Season"
    assert names.title(defn, 1, "demo_1", "RU") == "Чемпион сезона «Демо-сезон»"


def test_buying_in_the_season_shop_earns_window_shopper(
    session, active_season, tagged_game, shop_open, buyer
) -> None:
    _credit_coins(session, active_season, 200)
    season_shop.buy(session, active_season, _run(), buyer, DANGO)
    assert _grants(session, "season_window_shopper") == [(WINNER, 1, "demo_1")]


def test_window_shopper_is_granted_once_despite_its_own_reward(
    session, active_season, tagged_game, shop_open, buyer
) -> None:
    _credit_coins(session, active_season, 200)
    season_shop.buy(session, active_season, _run(), buyer, DANGO)
    season_shop.buy(session, active_season, _run(), buyer, BORDER)
    count = session.scalar(
        select(func.count())
        .select_from(AchievementGrant)
        .where(AchievementGrant.key == "season_window_shopper")
    )
    assert count == 1
    paid = [
        e.data["reason"]
        for e in session.scalars(
            select(EventLog).where(EventLog.event_type == EventType.CURRENCY_MOVED)
        )
    ]
    assert CurrencyReason.ACHIEVEMENT.value in paid  # the reward re-entered the engine


def test_owning_the_whole_shop_earns_collector(
    session, active_season, tagged_game, shop_open, buyer
) -> None:
    _credit_coins(session, active_season, 200)
    season_shop.buy(session, active_season, _run(), buyer, DANGO)
    assert _grants(session, "season_collector") == []
    season_shop.buy(session, active_season, _run(), buyer, BORDER)
    assert _grants(session, "season_collector") == [(WINNER, 1, "demo_1")]


def test_five_season_wins_earn_on_theme(session, tagged_game, emit_win) -> None:
    for _ in range(4):
        emit_win(winner=WINNER, stage=2)
    assert _grants(session, "season_on_theme") == []
    emit_win(winner=WINNER, stage=2)
    assert _grants(session, "season_on_theme") == [(WINNER, 1, "demo_1")]


def test_the_eleven_season_definitions_close_the_static_catalogue() -> None:
    keys = [d.key for d in STATIC if d.key.startswith("season_")][:11]
    assert keys[0] == "season_debut"
    assert keys[-1] == "season_on_theme"
    assert EventType.SEASON_ENDED in catalogue.get("season_podium_1").triggers
