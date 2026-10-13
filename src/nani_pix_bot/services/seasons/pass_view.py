"""The Season Pass as rich-message markdown, for /seasonpass (pure; no
Telegram): a heading, the tiers as a table (free and premium in their own
columns, so neither wraps under the other), then the objectives under their
own heading."""

from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.season import SeasonPass, SeasonSchedule
from nani_pix_bot.seasons.definition import Reward, SeasonRun
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import goals, objectives
from nani_pix_bot.services.seasons.objectives import ObjectiveStatus
from nani_pix_bot.services.seasons.season_pass import describe, reached, xp_total
from nani_pix_bot.services.text import md_escape

REACHED, NOT_REACHED = "✅", "⬜"  # same width, unlike the old ▫️


def _table(run: SeasonRun, progress: SeasonPass, lang: str) -> list[str]:
    """One row per tier. Only the first cell carries a ✅/⬜ (the free track);
    premium cells are just the reward, and the Premium header carries the 🔒
    while Premium isn't owned."""
    with_premium = any(tier.premium for tier in run.tiers)
    head = ["", "XP", md_escape(i18n.t("season_pass.view.col_free", lang))]
    if with_premium:
        premium = md_escape(i18n.t("season_pass.view.premium_on", lang))
        head.append(premium if progress.premium_transfer_id is not None else f"{premium} 🔒")
    rows = [_row(head), "|" + "---|" * len(head)]
    for number, tier in enumerate(run.tiers, start=1):
        mark = REACHED if number <= progress.free_claimed else NOT_REACHED
        cells = [f"{mark} {number}", str(tier.xp), _reward(run, tier.free, lang)]
        if with_premium:
            cells.append(_reward(run, tier.premium, lang))
        rows.append(_row(cells))
    return rows


def _row(cells: list[str]) -> str:
    return "|" + "|".join(f" {cell} " if cell else " " for cell in cells) + "|"


def _reward(run: SeasonRun, reward: Reward | None, lang: str) -> str:
    return md_escape(describe(run, reward, lang)) if reward else "—"


def _objective_line(row: ObjectiveStatus, tz: ZoneInfo, lang: str) -> str:
    o = row.objective
    paid = {"name": o.name(lang), "reward": objectives.reward_text(o, lang)}
    if row.completed_at is not None:
        day = row.completed_at.astimezone(tz).strftime("%d.%m")
        return i18n.t("season_pass.view.objective_done", lang, date=day, **paid)
    return i18n.t(
        "season_pass.view.objective",
        lang,
        mark="🎯",
        goal=goals.describe(o.goal, lang),
        progress=row.progress,
        target=o.goal.target,
        **paid,
    )


def _objective_lines(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, lang: str
) -> list[str]:
    """The objectives section (seasons spec §5): empty when the run has none."""
    if not run.objectives:
        return []
    tz = settings.get_group_timezone(session)
    rows = objectives.status(session, season, run, player_id)
    header = ["", "## " + md_escape(i18n.t("season_pass.view.objectives", lang))]
    return header + [md_escape(_objective_line(row, tz, lang)) for row in rows]


def view_markdown(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, lang: str
) -> str:
    """The pass as the player sees it in /seasonpass: progress, balance,
    the Premium price while not owned, the tier table, then the season's
    objectives."""
    total = xp_total(session, season.id, player_id)
    # No pass row yet reads as an untouched one (transient, never added).
    progress = session.get(SeasonPass, (season.id, player_id)) or SeasonPass(
        season_id=season.id, player_id=player_id, free_claimed=0, premium_claimed=0
    )
    level = reached(run, total)
    status = [i18n.t("season_pass.view.progress", lang, xp=total, tier=level, tiers=len(run.tiers))]
    if level < len(run.tiers):
        status.append(i18n.t("season_pass.view.next", lang, xp=run.tiers[level].xp))
    coins = wallet.balance(session, player_id, season_id=season.id)
    status.append(i18n.t("season_pass.view.coins", lang, coins=coins))
    if progress.premium_transfer_id is None:
        status.append(i18n.t("season_pass.view.premium_off", lang, price=run.premium_price))
    lines = [
        "## " + md_escape(i18n.t("season_pass.view.header", lang, season=run.name(lang))),
        *(md_escape(line) for line in status),
        "",
        *_table(run, progress, lang),
        *_objective_lines(session, season, run, player_id, lang),
    ]
    return "\n".join(lines)
