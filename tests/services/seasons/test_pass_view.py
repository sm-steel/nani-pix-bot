from dataclasses import replace
from datetime import UTC, datetime

from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.season import SeasonObjective
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.seasons import pass_view
from tests.seasons_fixtures import WINNER


def _demo() -> SeasonRun:
    run = registry.get("demo_1")
    assert run is not None
    return run


def test_objectives_show_progress(session, active_season, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    session.add(BotSettings(id=settings.SETTINGS_ID, quiet_timezone="Europe/Moscow"))
    # 21:30 UTC on Nov 2 is already Nov 3 in the group's timezone.
    row = session.get(SeasonObjective, (active_season.id, WINNER, "first_bloom"))
    row.completed_at = datetime(2026, 11, 2, 21, 30, tzinfo=UTC).replace(tzinfo=None)
    session.flush()
    lines = pass_view.view_lines(session, active_season, _demo(), WINNER, "EN")
    assert i18n.t("season_pass.view.objectives", "EN") in lines
    done = next(line for line in lines if line.startswith("✅ First Bloom"))
    assert "03.11" in done
    assert any(line.startswith("🎯 Hanami Host") and "(0/2)" in line for line in lines)


def test_a_run_without_objectives_shows_no_section(session, active_season, tagged_game) -> None:
    run = replace(_demo(), objectives=())
    lines = pass_view.view_lines(session, active_season, run, WINNER, "EN")
    assert i18n.t("season_pass.view.objectives", "EN") not in lines
