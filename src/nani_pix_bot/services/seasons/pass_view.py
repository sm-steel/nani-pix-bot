"""The Season Pass as text, for /seasonpass (pure; no Telegram)."""

from sqlalchemy.orm import Session

from nani_pix_bot.models.season import SeasonPass, SeasonSchedule
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons.season_pass import describe, reached, xp_total


def _tier_line(run: SeasonRun, number: int, progress: SeasonPass, lang: str) -> str:
    tier = run.tiers[number - 1]
    free = describe(run, tier.free, lang) if tier.free else "—"
    line = f"{'✅' if number <= progress.free_claimed else '▫️'} {number}. {tier.xp} XP — {free}"
    if tier.premium is not None:
        got = number <= progress.premium_claimed
        line += f" | {'✅' if got else '🔒'} ⭐ {describe(run, tier.premium, lang)}"
    return line


def view_lines(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, lang: str
) -> list[str]:
    """The pass as the player sees it in /seasonpass: progress, balance,
    Premium state and one line per tier."""
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
    return lines
