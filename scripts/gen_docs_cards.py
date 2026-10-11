"""Render the user's guide's example achievement cards.

Builds a gold unlock card and a yearly champions card the way
`jobs/announcements.py` and `services/achievements/podium.py` do, from the
bot's own strings, default rewards and card backgrounds, then draws them with
the bot's own renderer, once per language. The players are made up and have
no avatar, so the cards show the initials faces the bot falls back to.
Called by `docs/site`'s predev/prebuild npm scripts; the output directory is
gitignored.

It also draws the first season's (Sakura Season) cards with the production
renderers and its committed art: the season-start post, a profile card over
each of its three banners, and a profile card showing the stamps. Those are
WebP: the renders are photographs and would weigh about a megabyte each as PNG.

Usage: uv run python scripts/gen_docs_cards.py
"""

from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from nani_pix_bot.models.enums import CosmeticSlot, OutboxKind, Rarity
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements import catalogue, names
from nani_pix_bot.services.achievements.definitions import threshold
from nani_pix_bot.services.achievements.rewards import POINTS
from nani_pix_bot.services.cards import (
    BadgeEntry,
    PodiumCard,
    PodiumEntry,
    ProfileCard,
    SeasonBanner,
    StampEntry,
    UnlockCard,
    load_background,
    podium_slot,
    render_podium_card,
    render_profile_card,
    render_season_banner,
    render_unlock_card,
    season_background,
    season_stamp,
    unlock_slot,
)
from nani_pix_bot.services.cosmetics import looks, profile
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


RUN_ID = "sakura_1"
WEBP_QUALITY = 85
# The made-up season dates the start card shows, written the way the bot writes them.
SEASON_DATES = "12.10 18:00 \u2013 22.10 18:00 (Europe/Moscow)"
# The Sakura profile cards: a made-up player with a Sakura title, two achievements
# (a Sakura one and a gold Sharpshooter), 2 seasons played and 12 cosmetics owned.
TITLE_KEY = "title.hanami_host"
RUN_BADGE = ("hanami_party", Rarity.BRONZE)
PROFILE_SEASONS, PROFILE_COSMETICS = 2, 12


def webp(png: bytes) -> bytes:
    """A render re-encoded as lossy WebP, to keep the guide's images light."""
    out = BytesIO()
    with Image.open(BytesIO(png)) as image:
        image.save(out, "WEBP", quality=WEBP_QUALITY)
    return out.getvalue()


def run() -> SeasonRun:
    found = registry.get(RUN_ID)
    if found is None:
        raise LookupError(f"{RUN_ID} is not in the season registry")
    return found


def season_banner(lang: str) -> SeasonBanner:
    return SeasonBanner(
        headline=i18n.t(f"card.season.{OutboxKind.SEASON_START.value}", lang),
        title=run().name(lang.upper()),
        subtitle=SEASON_DATES,
        stamp=season_stamp(RUN_ID, plus=False),
    )


def plain_season_banner(lang: str) -> SeasonBanner:
    """The season's card with no ribbon and no dates: the Sakura page's own picture."""
    return replace(season_banner(lang), headline="", subtitle="")


def banner_files() -> tuple[str, ...]:
    """The run's profile-banner file names, in the order the run lists them."""
    return tuple(i.look for i in run().items if i.slot is CosmeticSlot.PROFILE_BANNER)


def _profile(lang: str, stamps: tuple[StampEntry, ...]) -> ProfileCard:
    sakura = run()
    title = next(i for i in sakura.items if i.key == TITLE_KEY)
    run_badge = next(a for a in sakura.achievements if a.key == RUN_BADGE[0])
    sharpshooter = catalogue.get(ACHIEVEMENT)
    tier = sharpshooter.rarities.index(Rarity.GOLD) + 1
    return ProfileCard(
        name=PLAYER,
        title=title.name(lang.upper()),
        stamps=stamps,
        stats=profile.stats(PROFILE_SEASONS, PROFILE_COSMETICS, lang),
        seed=UNLOCK_SEED,
        badges=(
            BadgeEntry(names.title(sharpshooter, tier, "", lang), Rarity.GOLD),
            BadgeEntry(run_badge.name(lang.upper()), RUN_BADGE[1]),
        ),
    )


def _stamp(lang: str, *, gold: bool, plus: bool) -> StampEntry:
    return StampEntry(run().name(lang.upper()), gold=gold, art=season_stamp(RUN_ID, plus=plus))


def banner_card(lang: str) -> ProfileCard:
    """The profile card over a Sakura banner, with the season's plain stamp."""
    return _profile(lang, (_stamp(lang, gold=False, plus=False),))


def stamps_card(lang: str) -> ProfileCard:
    """The profile card with the three stamps a Sakura player can hold: scored only,
    pass completed (gold ring), and Sakura+ (gold ring and gold art)."""
    return _profile(
        lang,
        (
            _stamp(lang, gold=False, plus=False),
            _stamp(lang, gold=True, plus=False),
            _stamp(lang, gold=True, plus=True),
        ),
    )


def banner_stem(file_name: str) -> str:
    return file_name.rsplit(".", 1)[0].replace("_", "-")


def sakura_images(lang: str) -> dict[str, bytes]:
    """The Sakura images of one language, by file name. The stamps card sits on the
    key visual, which is what a card with no equipped banner shows."""
    key_visual = season_background(RUN_ID)
    files = {
        f"season-sakura-{lang}.webp": webp(render_season_banner(season_banner(lang), key_visual)),
        f"season-sakura-plain-{lang}.webp": webp(
            render_season_banner(plain_season_banner(lang), key_visual)
        ),
        f"profile-stamps-{lang}.webp": webp(
            render_profile_card(stamps_card(lang), None, key_visual)
        ),
    }
    for file_name in banner_files():
        banner = looks.banner_path(RUN_ID, file_name).read_bytes()
        files[f"profile-{banner_stem(file_name)}-{lang}.webp"] = webp(
            render_profile_card(banner_card(lang), None, banner)
        )
    return files


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for lang in LANGS:
        background = load_background(unlock_slot(Rarity.GOLD), UNLOCK_SEED)
        unlock = render_unlock_card(unlock_card(lang), None, background)
        (out_dir / f"unlock-{lang}.png").write_bytes(unlock)
        podium = podium_card(lang)
        background = load_background(podium_slot("year"), podium.entries[0].seed)
        (out_dir / f"podium-{lang}.png").write_bytes(render_podium_card(podium, background))
        for file_name, data in sakura_images(lang).items():
            (out_dir / file_name).write_bytes(data)


if __name__ == "__main__":
    generate(OUT_DIR)
