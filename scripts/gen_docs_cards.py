"""Render the user's guide's example achievement cards.

Builds a gold unlock card and a yearly champions card the way
`jobs/announcements.py` and `services/achievements/podium.py` do, from the
bot's own strings, default rewards and card backgrounds, then draws them with
the bot's own renderer, once per language. The players are made up and have
no avatar, so the cards show the initials faces the bot falls back to.
Called by `docs/site`'s predev/prebuild npm scripts; the output directory is
gitignored.

Usage: uv run python scripts/gen_docs_cards.py
"""

from pathlib import Path

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements import catalogue, names
from nani_pix_bot.services.achievements.definitions import threshold
from nani_pix_bot.services.achievements.rewards import POINTS
from nani_pix_bot.services.cards import (
    PodiumCard,
    PodiumEntry,
    UnlockCard,
    load_background,
    podium_slot,
    render_podium_card,
    render_unlock_card,
    unlock_slot,
)
from nani_pix_bot.services.economy.config import DEFAULT_AMOUNTS, EconomyKey

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "site" / "src" / "assets" / "generated"
LANGS = ("en", "ru")

ACHIEVEMENT = "sharpshooter"
PLAYER = "@hana"
# @hana's made-up player id. The bot seeds the avatar colour and the
# background pick with it; the podium players are ids 1, 2, 3 in order.
UNLOCK_SEED = 1
YEAR = "2025"
# (name, 🌟 score, wins), in podium order.
PODIUM = (("@hana", 64, 21), ("@kenji", 51, 17), ("@mio", 38, 12))


def unlock_card(lang: str) -> UnlockCard:
    defn = catalogue.get(ACHIEVEMENT)
    tier = defn.rarities.index(Rarity.GOLD) + 1
    return UnlockCard(
        headline=i18n.t("card.unlocked", lang),
        name=names.title(defn, tier, "", lang),
        description=i18n.t(f"achievement.{ACHIEVEMENT}.desc", lang, n=threshold(defn, tier)),
        handle=PLAYER,
        reward=DEFAULT_AMOUNTS[EconomyKey.ACHIEVEMENT_GOLD],
        points_label=i18n.t("card.points", lang, points=POINTS[Rarity.GOLD]),
        rarity_label=i18n.t("achievement.rarity.gold", lang),
        rarity=Rarity.GOLD,
        seed=UNLOCK_SEED,
    )


def podium_card(lang: str) -> PodiumCard:
    entries = tuple(
        PodiumEntry(
            name,
            i18n.t("card.score", lang, score=score, wins=wins),
            seed=place,  # the player id: places 1, 2, 3 are ids 1, 2, 3
            rank=place,
        )
        for place, (name, score, wins) in enumerate(PODIUM, start=1)
    )
    title = i18n.t("card.podium.year", lang, period=names.period_label(YEAR, lang))
    return PodiumCard(title, entries)


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for lang in LANGS:
        background = load_background(unlock_slot(Rarity.GOLD), UNLOCK_SEED)
        unlock = render_unlock_card(unlock_card(lang), None, background)
        (out_dir / f"unlock-{lang}.png").write_bytes(unlock)
        podium = podium_card(lang)
        background = load_background(podium_slot("year"), podium.entries[0].seed)
        (out_dir / f"podium-{lang}.png").write_bytes(render_podium_card(podium, background))


if __name__ == "__main__":
    generate(OUT_DIR)
