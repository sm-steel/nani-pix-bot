"""A player's profile (seasons spec §5): season stamps and achievements (as
picked in showcase.py), equipped banner and title, and the counts the card and
caption show."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import catalogue, names, titles
from nani_pix_bot.services.cards import StatEntry, season_background
from nani_pix_bot.services.cosmetics import inventory, looks, showcase


@dataclass(frozen=True)
class Stamp:
    run_id: str
    label: str
    gold: bool  # pass completed (Phase 3); never set yet


@dataclass(frozen=True)
class Badge:
    label: str  # the achievement's name with its tier
    rarity: Rarity


@dataclass(frozen=True)
class ProfileView:
    player_id: int
    name: str  # plain: the card can't draw emoji, so no flair here
    title: str | None
    banner: bytes | None
    stamps: tuple[Stamp, ...]  # picked order, else newest season first
    badges: tuple[Badge, ...]  # picked order, else rarest first
    seasons_played: int
    cosmetics_owned: int


def stats(seasons: int, owned: int, lang: str) -> tuple[StatEntry, ...]:
    """The profile card's stats line: seasons played, cosmetics owned."""
    return (
        StatEntry("medal", i18n.t("profile.stat_seasons", lang, seasons=seasons)),
        StatEntry("gift", i18n.t("profile.stat_cosmetics", lang, owned=owned)),
    )


def roster(session: Session) -> list[int]:
    """Every known player, in the order the /profile list and ◀ ▶ walk them:
    by display name (no "@", case ignored), then by id."""
    ids = session.scalars(select(Player.telegram_user_id))

    def key(player_id: int) -> tuple[str, int]:
        return players.display_name(session, player_id).lstrip("@").casefold(), player_id

    return sorted(ids, key=key)


def neighbour(order: list[int], current: int, step: int) -> int:
    """The player `step` places from `current` in `order`, wrapping at both ends;
    the first one when `current` is no longer listed."""
    if current not in order:
        return order[0]
    return order[(order.index(current) + step) % len(order)]


def _stamp(run_id: str, lang: str) -> Stamp:
    run = registry.get(run_id)
    return Stamp(run_id, run.name(lang) if run else run_id, gold=False)


def _badges(session: Session, player_id: int, lang: str) -> tuple[Badge, ...]:
    """The shown achievements, named; one whose key left the catalogue is skipped."""
    shown = []
    for held in showcase.shown_badges(session, player_id):
        try:
            defn = catalogue.get(held.key)
        except KeyError:
            continue
        shown.append(Badge(names.name(defn, held.tier, lang), held.rarity))
    return tuple(shown)


def _banner(session: Session, player_id: int, played: list[str]) -> bytes | None:
    """The equipped banner, else the key visual of the last season played."""
    equipped = inventory.equipped(session, player_id, CosmeticSlot.PROFILE_BANNER)
    if equipped is not None:
        return looks.banner_path(equipped.run.run_id, equipped.item.look).read_bytes()
    return season_background(played[0]) if played else None


def build(session: Session, player_id: int, lang: str) -> ProfileView:
    played = showcase.stamp_choices(session, player_id)
    stamps = tuple(_stamp(run_id, lang) for run_id in showcase.shown_stamps(session, player_id))
    player = session.get(Player, player_id)
    owned = session.scalar(
        select(func.count()).select_from(OwnedItem).where(OwnedItem.player_id == player_id)
    )
    return ProfileView(
        player_id=player_id,
        name=players.display_name(session, player_id),
        title=titles.text(player.title_key if player else None, lang),
        banner=_banner(session, player_id, played),
        stamps=stamps,
        badges=_badges(session, player_id, lang),
        seasons_played=len(played),
        cosmetics_owned=owned or 0,
    )
