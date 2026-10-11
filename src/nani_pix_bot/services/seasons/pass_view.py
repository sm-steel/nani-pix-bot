"""The Season Pass as text, for /seasonpass (pure; no Telegram)."""

from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.season import SeasonPass, SeasonSchedule
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import goals, objectives
from nani_pix_bot.services.seasons.objectives import ObjectiveStatus
from nani_pix_bot.services.seasons.season_pass import describe, reached, xp_total


def _tier_line(run: SeasonRun, number: int, progress: SeasonPass, lang: str) -> str:
    tier = run.tiers[number - 1]
    free = describe(run, tier.free, lang) if tier.free else "—"
    line = f"{'✅' if number <= progress.free_claimed else '▫️'} {number}. {tier.xp} XP — {free}"
    if tier.premium is not None:
        got = number <= progress.premium_claimed
        line += f" | {'✅' if got else '🔒'} ⭐ {describe(run, tier.premium, lang)}"
    return line


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
    header = ["", i18n.t("season_pass.view.objectives", lang)]
    return header + [_objective_line(row, tz, lang) for row in rows]


def view_lines(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, lang: str
) -> list[str]:
    """The pass as the player sees it in /seasonpass: progress, balance,
    Premium state, one line per tier, then the season's objectives."""
    total = xp_total(session, season.id, player_id)
    # No pass row yet reads as an untouched one (transient, never added).
    progress = session.get(SeasonPass, (season.id, player_id)) or SeasonPass(
        season_id=season.id, player_id=player_id, free_claimed=0, premium_claimed=0
    )
    level = reached(run, total)
    lines = [
        i18n.t("season_pass.view.header", lang, season=run.name(lang)),
        i18n.t("season_pass.view.progress", lang, xp=total, tier=level, tiers=len(run.tiers)),
    ]
    if level < len(run.tiers):
        lines.append(i18n.t("season_pass.view.next", lang, xp=run.tiers[level].xp))
    coins = wallet.balance(session, player_id, season_id=season.id)
    lines.append(i18n.t("season_pass.view.coins", lang, coins=coins))
    premium = progress.premium_transfer_id is not None
    key = "season_pass.view.premium_on" if premium else "season_pass.view.premium_off"
    lines.append(i18n.t(key, lang, price=run.premium_price))
    lines.append("")
    lines += [_tier_line(run, n, progress, lang) for n in range(1, len(run.tiers) + 1)]
    return lines + _objective_lines(session, season, run, player_id, lang)
