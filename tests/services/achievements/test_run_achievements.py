from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import catalogue, names, status
from nani_pix_bot.services.achievements.definitions import Kind
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements

KEY = "demo_1.double_date"


@pytest.fixture(autouse=True)
def _season_runs(fake_runs, monkeypatch):
    """Only run achievements evaluate: the minimal events these tests emit lack
    fields the static definitions read."""
    monkeypatch.setattr(catalogue, "CATALOGUE", ())


@pytest.fixture
def second_season_game(session) -> Game:
    """A game tagged to an ACTIVE season of run demo_2."""
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
        current_stage=PixelStage.STAGE_1,
        title_english="Naruto",
        season_id=season.id,
    )
    session.add(game)
    session.flush()
    return game


def _grants(session, key: str) -> list[tuple[int, int, str]]:
    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.key == key))
    return [(r.player_id, r.tier, r.period_key) for r in rows]


def test_run_achievements_join_the_catalogue() -> None:
    defn = catalogue.get(KEY)
    assert defn.kind is Kind.SEASON
    assert defn.run_id == "demo_1"
    assert KEY in [d.key for d in catalogue.definitions()]


def test_an_unknown_run_achievement_is_still_a_key_error() -> None:
    with pytest.raises(KeyError):
        catalogue.get("demo_1.nope")


def test_the_run_definitions_are_cached_per_registry(monkeypatch) -> None:
    assert catalogue._run_definitions() is catalogue._run_definitions()
    monkeypatch.setattr(registry, "all_runs", dict)
    assert catalogue._run_definitions() == ()


def test_two_wins_in_one_day_earn_it(session, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    assert _grants(session, KEY) == []
    emit_win(winner=WINNER, stage=2)
    assert _grants(session, KEY) == [(WINNER, 1, "demo_1")]


def test_a_run_achievement_fires_only_in_its_own_season(session, second_season_game) -> None:
    for stage in (1, 2):
        events.emit(
            session,
            EventType.GAME_WON,
            events.Involved(actor_id=WINNER, subject_id=STARTER, game_id=second_season_game.id),
            stage=stage,
            hard_mode=False,
        )
    assert _grants(session, KEY) == []


def test_its_name_and_description_come_from_the_run_module() -> None:
    defn = catalogue.get(KEY)
    assert names.name(defn, 1, "RU") == "Двойное свидание"
    assert names.name(defn, 0, "EN") == "Double Date"
    assert names.description(defn, 1, "EN") == "Win two season games in one day"
    assert names.description(defn, 1, "RU") == "Выиграйте две игры сезона за один день"
    assert names.name(defn, 1, "DE") == "Double Date"


def test_the_achievement_list_includes_it(session) -> None:
    session.add(Player(telegram_user_id=WINNER))
    session.flush()
    items = {s.defn.key: s for s in status.build(session, WINNER, datetime.now(UTC))}
    assert items[KEY].state is status.State.UNTOUCHED
