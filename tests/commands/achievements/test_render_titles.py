from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.commands.achievements import compare, render
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.achievements import catalogue, engine, names, status
from nani_pix_bot.services.achievements.definitions import Definition, Kind
from nani_pix_bot.services.achievements.status import State, Status
from tests.hex_fixtures import add_hex

pytestmark = pytest.mark.achievements


def _titled(session: Session) -> None:
    session.add(Player(telegram_user_id=1, username="a_b", title_key="champion_month:1:2026-10"))
    session.flush()
    engine.grant(session, engine.GrantRequest(1, "champion_month", 1, "2026-10"))


def test_the_summary_shows_the_chosen_title(session: Session) -> None:
    _titled(session)

    text = render.summary(session, 1, "en", datetime.now(UTC))

    assert "Title: Champion of October 2026" in text


def test_the_top_table_shows_an_escaped_title_next_to_the_name(session: Session) -> None:
    _titled(session)

    text = render.top_table(session, status.top(session, limit=10), "en", 0)

    assert chr(92).join(["a", "_b «Champion of October 2026»"]) in text


def test_a_faceless_player_shows_no_title_in_the_summary_or_the_top(session: Session) -> None:
    _titled(session)
    add_hex(session, 1, HexKind.NOPPERABO, hours=1)

    summary = render.summary(session, 1, "en", datetime.now(UTC))
    table = render.top_table(session, status.top(session, limit=10), "en", 0)

    assert "Title:" not in summary
    assert "Champion of October" not in table
    assert "👤" in summary


def test_an_earned_champion_row_shows_the_current_race_rank() -> None:
    week = catalogue.get("champion_week")
    racing = Status(week, State.EARNED, tier=1, period_key="2026-W40", rank=2)

    line = render.row(racing, "en", None)

    assert " · " + md_escape("now #2") + " —" in line  # after the times-won count
    assert md_escape("сейчас #2") in render.row(racing, "ru", None)


def test_an_earned_champion_out_of_the_race_shows_no_rank() -> None:
    week = catalogue.get("champion_week")
    resting = Status(week, State.EARNED, tier=1, period_key="2026-W40")

    assert "now" not in render.row(resting, "en", None)


def test_an_open_milestone_names_the_next_threshold_and_the_group_count() -> None:
    keeper = catalogue.get("milestone_keeper")
    open_keeper = Status(keeper, State.UNTOUCHED, value=137, target=250)

    line = render.row(open_keeper, "en", None)

    assert md_escape("250th solved game") in line
    assert md_escape("solved so far: 137/250") in line
    assert md_escape("решено: 137/250") in render.row(open_keeper, "ru", None)


def test_an_open_milestone_before_the_first_tier_names_the_first_threshold() -> None:
    keeper = catalogue.get("milestone_keeper")

    line = render.row(Status(keeper, State.UNTOUCHED, value=12, target=100), "en", None)

    assert md_escape("100th solved game") in line
    assert md_escape("12/100") in line


def _season_def(**overrides) -> Definition:
    plain = Definition("sakura_rush", Kind.SEASON, (1,), (Rarity.BRONZE,), frozenset(), _no_wins)
    return replace(plain, **overrides)


def _no_wins(_history: object) -> int:
    return 0


def test_an_earned_season_row_counts_seasons_and_names_the_latest(fake_runs) -> None:
    earned = Status(_season_def(), State.EARNED, tier=2, period_key="demo_1")

    line = render.row(earned, "en", None)

    assert "\N{MULTIPLICATION SIGN}2" in line
    assert md_escape("Demo Season") in line


def test_a_titled_season_row_shows_its_title(fake_runs, monkeypatch) -> None:
    monkeypatch.setattr(names.i18n, "t", lambda key, lang, **kwargs: f"{key}|{kwargs}")
    earned = Status(_season_def(titled=True), State.EARNED, tier=1, period_key="demo_1")

    assert md_escape("achievement.sakura_rush.title|") in render.row(earned, "en", None)


def test_the_compare_cell_counts_seasons() -> None:
    earned = Status(_season_def(), State.EARNED, tier=3, period_key="demo_1")

    assert compare._cell(earned) == "✅ \N{MULTIPLICATION SIGN}3"


def test_the_summary_latest_line_names_an_untitled_season_achievement(
    session: Session, fake_runs
) -> None:
    session.add(Player(telegram_user_id=1, username="a_b"))
    session.flush()
    engine.grant(session, engine.GrantRequest(1, "season_debut", 1, "demo_1"))

    text = render.summary(session, 1, "en", datetime.now(UTC))

    assert "Latest: Season Debut" in text
    assert "achievement.season_debut" not in text
