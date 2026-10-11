from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import Rarity, SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements import catalogue, season_eval, status
from tests.seasons_fixtures import WINNER

pytestmark = pytest.mark.achievements


@pytest.fixture(autouse=True)
def _moscow(session, fake_runs) -> None:
    session.add(BotSettings(id=settings.SETTINGS_ID, quiet_timezone="Europe/Moscow"))
    session.flush()


def _days(session, *, start_at, end_at, started_at=None) -> tuple[date, date]:
    season = SeasonSchedule(
        run_id="demo_1",
        start_at=start_at,
        end_at=end_at,
        started_at=started_at,
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.flush()
    run = registry.get("demo_1")
    assert run is not None
    scoped = season_eval.scoped_history(session, season, run, WINNER)
    return scoped.first_day, scoped.last_day


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_first_day_comes_from_started_at_in_the_group_zone(session) -> None:
    first, _ = _days(session, start_at=_utc(1, 0), end_at=_utc(10, 12), started_at=_utc(1, 22))
    assert first == date(2026, 11, 2)  # 22:00 UTC is 01:00 the next day in Moscow


def test_first_day_falls_back_to_start_at(session) -> None:
    first, _ = _days(session, start_at=_utc(1, 22), end_at=_utc(10, 12))
    assert first == date(2026, 11, 2)


def test_a_season_ending_at_local_midnight_ends_the_day_before(session) -> None:
    _, last = _days(session, start_at=_utc(1, 0), end_at=_utc(10, 21))
    assert last == date(2026, 11, 10)  # 21:00 UTC is exactly 00:00 on the 11th in Moscow


def test_a_season_ending_after_local_midnight_ends_that_day(session) -> None:
    _, last = _days(session, start_at=_utc(1, 0), end_at=_utc(10, 21, 30))
    assert last == date(2026, 11, 11)  # 21:30 UTC is 00:30 on the 11th in Moscow


def test_a_guess_on_the_first_local_day_earns_the_opening_act(
    session, active_season, tagged_game, emit_guess
) -> None:
    now = datetime.now(UTC)
    active_season.start_at = now - timedelta(minutes=1)
    active_season.end_at = now + timedelta(days=9)
    session.flush()
    emit_guess(player=WINNER)
    keys = {g.key for g in session.query(AchievementGrant).filter_by(player_id=WINNER)}
    assert "season_opening_act" in keys
    assert "season_curtain_call" not in keys


def test_two_grants_of_one_season_count_as_one_season(session) -> None:
    defn = catalogue.get("season_debut")
    now = datetime.now(UTC)
    rows = tuple(
        AchievementGrant(
            player_id=WINNER,
            key=defn.key,
            tier=tier,
            period_key="demo_1",
            rarity=Rarity.BRONZE,
            reward=0,
            points=0,
            granted_at=now + timedelta(seconds=tier),
        )
        for tier in (1, 2)
    )
    ctx = status._Context(cast(Any, session), cast(Any, None), rows, now)
    assert status._earned(defn, ctx).tier == 1
