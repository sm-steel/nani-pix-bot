"""The Sakura Season's own achievements, end to end: the real registry, an
ACTIVE sakura_1 season, and real events through events.emit."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models import event_log
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import (
    EventType,
    GameStatus,
    ItemSource,
    PixelStage,
    SeasonStatus,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events, settings
from nani_pix_bot.services.achievements import catalogue
from nani_pix_bot.services.cosmetics.inventory import ItemGrant, grant
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements

RUN_ID = "sakura_1"
GUESSERS = (3, 4, 5)


@pytest.fixture(autouse=True)
def _real_runs(monkeypatch):
    """The shipped registry; only run achievements evaluate, as the minimal
    events emitted here lack fields the static definitions read."""
    registry.all_runs.cache_clear()
    registry.all_items.cache_clear()
    monkeypatch.setattr(catalogue, "CATALOGUE", ())
    yield
    registry.all_items.cache_clear()


class _Clock:
    """What every event's occurred_at reads (event_log's column default)."""

    def __init__(self) -> None:
        self.at = NOW

    def set(self, at: datetime) -> None:
        self.at = at


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> _Clock:
    """Fixed event times, never the real clock: a same-day test can't fail
    around midnight."""
    fixed = _Clock()

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.at.astimezone(tz or UTC)

    monkeypatch.setattr(event_log, "datetime", _Frozen)
    return fixed


@pytest.fixture
def sakura_season(session) -> SeasonSchedule:
    row = SeasonSchedule(
        run_id=RUN_ID,
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.add_all([Player(telegram_user_id=u) for u in (WINNER, STARTER, *GUESSERS)])
    session.flush()
    return row


def _game(session, season: SeasonSchedule) -> Game:
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Clannad",
        season_id=season.id,
    )
    session.add(game)
    session.flush()
    return game


def _win(
    session, game: Game, *, guessers: int = 1, how: str = "guess", hard_mode: bool = False
) -> None:
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=WINNER, subject_id=STARTER, game_id=game.id),
        how=how,
        stage=2,
        hard_mode=hard_mode,
        distinct_guessers=guessers,
    )


def _holders(session, key: str) -> list[int]:
    rows = session.scalars(
        select(AchievementGrant).where(AchievementGrant.key == f"{RUN_ID}.{key}")
    )
    return sorted(r.player_id for r in rows if r.period_key == RUN_ID)


def test_two_wins_on_one_day_earn_kokuhaku(session, sakura_season) -> None:
    _win(session, _game(session, sakura_season))
    assert _holders(session, "kokuhaku") == []
    _win(session, _game(session, sakura_season))
    assert _holders(session, "kokuhaku") == [WINNER]


def test_one_game_won_twice_is_no_kokuhaku(session, sakura_season) -> None:
    game = _game(session, sakura_season)
    _win(session, game)
    _win(session, game, how="setwinner")  # a re-finish of the same game
    assert _holders(session, "kokuhaku") == []


def _moscow(session) -> None:
    session.add(BotSettings(id=settings.SETTINGS_ID, quiet_timezone="Europe/Moscow"))
    session.flush()


# 22:30 UTC on 1 Nov and 05:00 UTC on 2 Nov: two UTC days, one Moscow day (2 Nov).
_LATE = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
_EARLY = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


def test_kokuhaku_counts_days_in_the_group_timezone(session, sakura_season, clock) -> None:
    _moscow(session)
    clock.set(_LATE)
    _win(session, _game(session, sakura_season))
    clock.set(_EARLY)
    _win(session, _game(session, sakura_season))
    assert _holders(session, "kokuhaku") == [WINNER]


def test_the_same_two_wins_are_two_days_in_utc(session, sakura_season, clock) -> None:
    clock.set(_LATE)
    _win(session, _game(session, sakura_season))
    clock.set(_EARLY)
    _win(session, _game(session, sakura_season))
    assert _holders(session, "kokuhaku") == []


def test_a_game_with_three_guessers_is_no_party(session, sakura_season) -> None:
    _win(session, _game(session, sakura_season), guessers=3)
    assert _holders(session, "hanami_party") == []


def test_a_game_with_four_guessers_earns_hanami_party(session, sakura_season) -> None:
    game = _game(session, sakura_season)
    for player in (WINNER, *GUESSERS):
        events.emit(session, EventType.GUESS, events.Involved(actor_id=player, game_id=game.id))
    _win(session, game, guessers=4)
    # The winner guessed in it and the starter hosted it.
    assert _holders(session, "hanami_party") == [WINNER, STARTER]


def test_a_winner_named_without_a_guess_earns_the_party(session, sakura_season) -> None:
    """/setwinner or a vote may name a winner who never guessed in the game."""
    _win(session, _game(session, sakura_season), guessers=4, how="setwinner")
    assert _holders(session, "hanami_party") == [WINNER, STARTER]


def test_a_hard_mode_party_counts_too(session, sakura_season) -> None:
    """Hanami Party is "play a game": HARD MODE games count like any other."""
    _win(session, _game(session, sakura_season), guessers=4, hard_mode=True)
    assert _holders(session, "hanami_party") == [WINNER, STARTER]


def test_an_unsolved_party_earns_it_for_the_host(session, sakura_season) -> None:
    game = _game(session, sakura_season)
    events.emit(
        session,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=STARTER, game_id=game.id),
        cause="timeout",
        hard_mode=False,
        distinct_guessers=4,
    )
    assert _holders(session, "hanami_party") == [STARTER]


def _grant_items(session, season: SeasonSchedule, keys) -> None:
    for key in keys:
        grant(session, WINNER, ItemGrant(f"{RUN_ID}/{key}", ItemSource.ADMIN, season.id))


def test_every_sakura_cosmetic_earns_full_bloom(session, sakura_season) -> None:
    keys = [item.key for item in registry.all_runs()[RUN_ID].items]
    assert len(keys) == 20
    _grant_items(session, sakura_season, keys[:-1])
    assert _holders(session, "full_bloom") == []
    _grant_items(session, sakura_season, keys[-1:])
    assert _holders(session, "full_bloom") == [WINNER]
