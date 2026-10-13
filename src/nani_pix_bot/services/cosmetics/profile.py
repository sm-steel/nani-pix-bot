"""A player's profile (seasons spec §5): season stamps and achievements (as
picked in showcase.py), equipped banner and title, and the counts the card and
caption show."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonPass
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RewardKind, SeasonRun
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import catalogue, names, titles
from nani_pix_bot.services.cards import (
    BadgeEntry,
    ProfileCard,
    StampEntry,
    StatEntry,
    season_background,
    season_stamp,
)
from nani_pix_bot.services.cosmetics import inventory, looks, showcase
from nani_pix_bot.services.game import history


@dataclass(frozen=True)
class Stamp:
    run_id: str
    label: str
    gold: bool  # the free pass reached its STAMP tier: the ring is gold
    art: bytes | None = None  # Sakura+ art if its premium tier was claimed, else the stamp art


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


def card(view: ProfileView, lang: str) -> ProfileCard:
    """The card /profile draws for `view` (and a banner preview puts a new banner behind)."""
    return ProfileCard(
        name=view.name,
        title=view.title,
        stamps=tuple(StampEntry(s.label, s.gold, s.art) for s in view.stamps),
        stats=stats(view.seasons_played, view.cosmetics_owned, lang),
        seed=view.player_id,
        badges=tuple(BadgeEntry(b.label, b.rarity) for b in view.badges),
    )


def stats(seasons: int, owned: int, lang: str) -> tuple[StatEntry, ...]:
    """The profile card's stats line: seasons played, cosmetics owned."""
    return (
        StatEntry("medal", i18n.t("profile.stat_seasons", lang, seasons=seasons)),
        StatEntry("gift", i18n.t("profile.stat_cosmetics", lang, owned=owned)),
    )


def roster(session: Session, *, viewer_id: int, bot_id: int) -> list[int]:
    """The players the /profile list and ◀ ▶ walk: everyone who played a
    finished game (hosted, won or guessed in it), most games first, then by
    display name (no "@", case ignored), then by id. The bot never shows,
    lurkers don't either, and the viewer is always in it (last, if they
    haven't played yet) so their own card is reachable."""
    played = history.games_played_counts(session)
    played.pop(bot_id, None)

    def key(player_id: int) -> tuple[int, str, int]:
        name = players.display_name(session, player_id).lstrip("@").casefold()
        return -played[player_id], name, player_id

    order = sorted(played, key=key)
    if viewer_id not in played and viewer_id != bot_id:
        order.append(viewer_id)
    return order


def neighbour(order: list[int], current: int, step: int) -> int:
    """The player `step` places from `current` in `order`, wrapping at both ends.
    From someone not listed (a non-player's card), ▶ leads to the first
    player and ◀ to the last."""
    if current not in order:
        return order[0] if step > 0 else order[-1]
    return order[(order.index(current) + step) % len(order)]


def _tier_number(run: SeasonRun, kind: RewardKind, *, premium: bool) -> int | None:
    """The 1-based tier whose free (or premium) reward is `kind`, if any."""
    for number, tier in enumerate(run.tiers, start=1):
        reward = tier.premium if premium else tier.free
        if reward is not None and reward.kind is kind:
            return number
    return None


def _stamp(
    session: Session, player_id: int, season_id: int | None, run_id: str, lang: str
) -> Stamp:
    """One season stamp: gold once the free STAMP tier is claimed; the Sakura+
    art once the premium STAMP_PLUS tier is, else the stamp art, else none."""
    run = registry.get(run_id)
    label = run.name(lang) if run else run_id
    progress = session.get(SeasonPass, (season_id, player_id)) if season_id is not None else None
    if run is None or progress is None:
        return Stamp(run_id, label, gold=False, art=season_stamp(run_id, plus=False))
    ring = _tier_number(run, RewardKind.STAMP, premium=False)
    plus = _tier_number(run, RewardKind.STAMP_PLUS, premium=True)
    gold = ring is not None and progress.free_claimed >= ring
    has_plus = plus is not None and progress.premium_claimed >= plus
    art = season_stamp(run_id, plus=True) if has_plus else None
    return Stamp(run_id, label, gold=gold, art=art or season_stamp(run_id, plus=False))


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
    # the newest season of a run wins (later dict entries overwrite earlier ones)
    seasons = {run_id: sid for sid, run_id in reversed(showcase.stamp_seasons(session, player_id))}
    stamps = tuple(
        _stamp(session, player_id, seasons.get(run_id), run_id, lang)
        for run_id in showcase.shown_stamps(session, player_id)
    )
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
