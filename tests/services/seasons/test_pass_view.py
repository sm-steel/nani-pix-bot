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


def _objective_rows(text: str, lang: str = "EN") -> list[str]:
    """The table lines under the objectives heading (header, rule, rows)."""
    lines = text.splitlines()
    start = lines.index("## " + i18n.t("season_pass.view.objectives", lang))
    return [line for line in lines[start + 1 :] if line.startswith("|")]


def _complete_first_bloom(session, active_season) -> None:
    session.add(BotSettings(id=settings.SETTINGS_ID, quiet_timezone="Europe/Moscow"))
    # 21:30 UTC on Nov 2 is already Nov 3 in the group's timezone.
    row = session.get(SeasonObjective, (active_season.id, WINNER, "first_bloom"))
    row.completed_at = datetime(2026, 11, 2, 21, 30, tzinfo=UTC).replace(tzinfo=None)
    session.flush()


def test_objectives_render_as_a_table(session, active_season, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    run = _demo()
    rows = _objective_rows(pass_view.view_markdown(session, active_season, run, WINNER, "EN"))
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert header == [
        "",
        *(
            i18n.t(f"season_pass.view.col_{col}", "EN")
            for col in ("objective", "goal", "progress", "reward")
        ),
    ]
    assert rows[1] == "|---|---|---|---|---|"
    assert len(rows) == 2 + len(run.objectives)


def test_open_objective_row_shows_progress_over_target(
    session, active_season, tagged_game, emit_win
) -> None:
    emit_win(winner=WINNER, stage=1)
    _complete_first_bloom(session, active_season)
    rows = _objective_rows(pass_view.view_markdown(session, active_season, _demo(), WINNER, "EN"))
    open_row = next(row for row in rows if "Hanami Host" in row)
    cells = [cell.strip() for cell in open_row.strip("|").split("|")]
    assert cells[0] == "🎯"
    assert cells[3] == "0/2"


def test_done_objective_row_shows_the_date_and_a_check(
    session, active_season, tagged_game, emit_win
) -> None:
    emit_win(winner=WINNER, stage=1)
    _complete_first_bloom(session, active_season)
    rows = _objective_rows(pass_view.view_markdown(session, active_season, _demo(), WINNER, "EN"))
    done = next(row for row in rows if "First Bloom" in row)
    cells = [cell.strip() for cell in done.strip("|").split("|")]
    assert cells[0] == pass_view.REACHED
    assert cells[3] == r"03\.11"  # the group's local day, markdown-escaped


def test_objective_cells_are_markdown_escaped(session, active_season, tagged_game) -> None:
    run = _demo()
    first = replace(run.objectives[0], names={"EN": "Pipe | Star*", "RU": "Pipe"})
    run = replace(run, objectives=(first, *run.objectives[1:]))
    rows = _objective_rows(pass_view.view_markdown(session, active_season, run, WINNER, "EN"))
    row = next(row for row in rows if "Pipe" in row)
    assert r"Pipe \| Star\*" in row
    assert row.replace(r"\|", "").count("|") == 6  # five cells, no stray separator


def test_no_objectives_no_section(session, active_season, tagged_game) -> None:
    run = replace(_demo(), objectives=())
    text = pass_view.view_markdown(session, active_season, run, WINNER, "EN")
    assert i18n.t("season_pass.view.objectives", "EN") not in text
    assert "🎯" not in text


def test_an_objective_without_coins_shows_no_coins(session, active_season, tagged_game) -> None:
    run = _demo()
    run = replace(run, objectives=tuple(replace(o, coins=0) for o in run.objectives))
    rows = _objective_rows(pass_view.view_markdown(session, active_season, run, WINNER, "EN"))[2:]
    assert len(rows) == len(run.objectives)
    assert all("🪙" not in line for line in rows)


def test_player_text_is_markdown_escaped(session, active_season, tagged_game) -> None:
    run = replace(_demo(), names={"EN": "Spring (2026)", "RU": "Весна (2026)"})
    text = pass_view.view_markdown(session, active_season, run, WINNER, "EN")
    assert r"Spring \(2026\)" in text.splitlines()[0]
