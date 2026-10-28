"""Yōkai Night's own achievements, end to end: the real registry, an ACTIVE
yokai_1 season, and real events through events.emit."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models import event_log
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import (
    EventType,
    GameStatus,
    HexOutcome,
    ItemSource,
    PixelStage,
    SeasonStatus,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RewardKind
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import catalogue
from nani_pix_bot.services.cosmetics.inventory import ItemGrant, grant
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements

RUN_ID = "yokai_1"
GUESSER = 3


@pytest.fixture(autouse=True)
def _real_runs(monkeypatch):
    """The shipped registry; only run achievements evaluate, as the minimal
    events emitted here lack fields the static definitions read."""
    registry.all_runs.cache_clear()
    registry.all_items.cache_clear()
    monkeypatch.setattr(catalogue, "CATALOGUE", ())
    yield
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch) -> None:
    """Fixed event times, never the real clock."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)

    monkeypatch.setattr(event_log, "datetime", _Frozen)


@pytest.fixture
def yokai_season(session) -> SeasonSchedule:
    row = SeasonSchedule(
        run_id=RUN_ID,
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=14),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.add_all([Player(telegram_user_id=u) for u in (WINNER, STARTER, GUESSER)])
    session.flush()
    return row


def _game(session, season: SeasonSchedule, **fields) -> Game:
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Mononoke",
        season_id=season.id,
        **fields,
    )
    session.add(game)
    session.flush()
    return game


def _guess(session, game: Game, player: int = GUESSER) -> None:
    """A guess row (end events evaluate every guesser) and its event."""
    session.add(GameGuess(game_id=game.id, player_id=player, text="x", stage=1))
    session.flush()
    events.emit(session, EventType.GUESS, events.Involved(actor_id=player, game_id=game.id))


def _end(session, game: Game, **facts) -> None:
    data = {"boss_game": False, "event_day": False, "haunted": False, "hard_mode": False}
    events.emit(
        session,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=STARTER, game_id=game.id),
        cause="timeout",
        **(data | facts),
    )


def _win(session, game: Game, *, how: str = "guess", **facts) -> None:
    data = {"boss_game": False, "event_day": False, "haunted": False, "hard_mode": False}
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=WINNER, subject_id=STARTER, game_id=game.id),
        how=how,
        stage=2,
        **(data | facts),
    )


def _held(session, key: str) -> dict[int, list[int]]:
    """player -> the tiers they hold of `key` this season."""
    rows = session.scalars(
        select(AchievementGrant).where(AchievementGrant.key == f"{RUN_ID}.{key}")
    )
    held: dict[int, list[int]] = {}
    for r in rows:
        if r.period_key == RUN_ID:
            held.setdefault(r.player_id, []).append(r.tier)
    return {player: sorted(tiers) for player, tiers in held.items()}


def test_gashadokuro_for_a_guesser_of_the_boss_game(session, yokai_season) -> None:
    plain = _game(session, yokai_season)
    _guess(session, plain)
    _end(session, plain)
    assert GUESSER not in _held(session, "gashadokuro")

    boss = _game(session, yokai_season, boss_game=True)
    _guess(session, boss)
    _end(session, boss, boss_game=True, haunted=True)
    assert _held(session, "gashadokuro")[GUESSER] == [1]
    assert _held(session, "gashadokuro_bane") == {}


def test_gashadokuro_for_the_winner_of_the_boss_game(session, yokai_season) -> None:
    boss = _game(session, yokai_season, boss_game=True)
    _win(session, boss, how="setwinner", boss_game=True, haunted=True)  # no guess row
    assert _held(session, "gashadokuro") == {WINNER: [1]}
    assert _held(session, "gashadokuro_bane") == {WINNER: [1]}


def _block(session, season: SeasonSchedule) -> None:
    events.emit(
        session,
        EventType.HEX_BLOCKED,
        events.Involved(actor_id=GUESSER, subject_id=WINNER),
        season_id=season.id,
        kind="kappa",
    )


def test_curse_breaker_climbs_with_each_block(session, yokai_season) -> None:
    _block(session, yokai_season)
    assert _held(session, "curse_breaker") == {GUESSER: [1]}
    _block(session, yokai_season)
    assert _held(session, "curse_breaker") == {GUESSER: [1, 2]}
    _block(session, yokai_season)
    assert _held(session, "curse_breaker") == {GUESSER: [1, 2]}
    _block(session, yokai_season)
    assert _held(session, "curse_breaker") == {GUESSER: [1, 2, 3]}


def _cast(session, season: SeasonSchedule, *, target: int, blocked=False, prank=False) -> None:
    events.emit(
        session,
        EventType.HEX_CAST,
        events.Involved(actor_id=WINNER, subject_id=target),
        season_id=season.id,
        kind="nopperabo",
        outcome=HexOutcome.BLOCKED.value if blocked else None,
        self_prank=prank,
    )


def test_hex_master_counts_only_hexes_that_land_on_others(session, yokai_season) -> None:
    _cast(session, yokai_season, target=GUESSER, blocked=True)
    _cast(session, yokai_season, target=WINNER, prank=True)  # omikuji's self-prank
    assert _held(session, "hex_master") == {}
    _cast(session, yokai_season, target=GUESSER)
    assert _held(session, "hex_master") == {WINNER: [1]}


def test_candle_snuffer_sums_the_candles_of_your_games(session, yokai_season) -> None:
    """Weights as the 100 Candles goal counts them: event day 3, haunted 2,
    plain 1, the boss 0 — a game you hosted counts like one you guessed in."""
    for facts in ({"boss_game": True, "haunted": True}, {}, {"haunted": True}):
        game = _game(session, yokai_season)
        _guess(session, game)
        _end(session, game, **facts)
    assert GUESSER not in _held(session, "candle_snuffer")  # 0 + 1 + 2
    _end(session, _game(session, yokai_season), event_day=True, haunted=True)  # not his game
    assert GUESSER not in _held(session, "candle_snuffer")
    game = _game(session, yokai_season)
    _guess(session, game)
    _end(session, game, event_day=True, haunted=True)  # + 3 = 6
    assert _held(session, "candle_snuffer")[GUESSER] == [1]
    assert _held(session, "candle_snuffer")[STARTER] == [1]  # hosted all five: 9


def test_night_parade_and_ghost_hunter_read_the_win_facts(session, yokai_season) -> None:
    _win(session, _game(session, yokai_season))
    assert _held(session, "night_parade") == {}
    assert _held(session, "ghost_hunter") == {}
    _win(session, _game(session, yokai_season), event_day=True, haunted=True)
    assert _held(session, "night_parade") == {WINNER: [1]}
    assert _held(session, "ghost_hunter") == {WINNER: [1]}


def test_hexed_and_happy(session, yokai_season) -> None:
    _win(session, _game(session, yokai_season), hexed=False)
    assert _held(session, "hexed_and_happy") == {}
    _win(session, _game(session, yokai_season), hexed=True)
    assert _held(session, "hexed_and_happy") == {WINNER: [1]}


def _draw(session, season: SeasonSchedule, outcome: str) -> None:
    events.emit(
        session,
        EventType.OMIKUJI_DRAWN,
        events.Involved(actor_id=GUESSER),
        season_id=season.id,
        outcome=outcome,
    )


def test_the_omikuji_achievements(session, yokai_season) -> None:
    for outcome in ("kyo", "kichi", "kyo"):
        _draw(session, yokai_season, outcome)
    assert _held(session, "fortune_seeker") == {GUESSER: [1]}
    assert _held(session, "cursed_luck") == {}
    assert _held(session, "daikichi") == {}
    _draw(session, yokai_season, "kyo")
    _draw(session, yokai_season, "daikichi")
    assert _held(session, "cursed_luck") == {GUESSER: [1]}
    assert _held(session, "daikichi") == {GUESSER: [1]}


def _pass_and_shop_keys() -> list[str]:
    run = registry.all_runs()[RUN_ID]
    passed = [
        reward.item_key
        for tier in run.tiers
        for reward in (tier.free, tier.premium)
        if reward is not None and reward.kind is RewardKind.ITEM
    ]
    return passed + [o.item_key for o in run.shop if o.kind is RewardKind.ITEM]


def _grant_items(session, season: SeasonSchedule, keys) -> None:
    for key in keys:
        grant(session, WINNER, ItemGrant(f"{RUN_ID}/{key}", ItemSource.ADMIN, season.id))


def test_hyakki_yagyo_needs_pass_and_shop_items_only(session, yokai_season) -> None:
    keys = _pass_and_shop_keys()
    assert len(keys) == 31
    assert "title.gashadokuro_slayer" not in keys
    assert "name_flair.mini_skeleton" not in keys
    _grant_items(session, yokai_season, keys[:-1])
    assert _held(session, "hyakki_yagyo") == {}
    _grant_items(session, yokai_season, keys[-1:])
    assert _held(session, "hyakki_yagyo") == {WINNER: [1]}
