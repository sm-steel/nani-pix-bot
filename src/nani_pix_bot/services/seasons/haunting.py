"""Haunted rounds (Halloween spec, "Yōkai Night"): the roll at activation,
the stored twist, and which twists a stage wears (a boss game stacks them). Must not import
services.game (activate_game calls in here)."""

import random
from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from typing import Protocol

from loguru import logger
from PIL import Image, ImageChops, ImageOps
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons import event_day, modifiers

# Twists that clear up at a haunted game's last stage; the mirror stays until
# the reveal. A boss game keeps every twist (see `fades`).
FADES_AT_LAST: frozenset[HauntTwist] = frozenset(
    {HauntTwist.TANUKI_LEAF, HauntTwist.YUREI_DOUBLE, HauntTwist.KYOKOTSU_XRAY}
)
# The Gashadokuro boss game's twists: stage N wears the first N (all four from
# stage 4 on), applied in this order.
BOSS_ORDER: tuple[HauntTwist, ...] = (
    HauntTwist.KITSUNE_MIRROR,
    HauntTwist.TANUKI_LEAF,
    HauntTwist.YUREI_DOUBLE,
    HauntTwist.KYOKOTSU_XRAY,
)

TANUKI_HUE_STEPS = 85  # of 256, about +120 degrees
YUREI_OFFSET = (0.06, 0.04)  # fraction of the width, the height
YUREI_ALPHA = 0.35
XRAY_DARK, XRAY_LIGHT = (10, 20, 40), (200, 240, 255)

_RNG = random.Random()  # noqa: S311 - a game's flavour, not a secret


class _Rng(Protocol):
    def randrange(self, stop: int, /) -> int: ...
    def choice(self, seq: Sequence[HauntTwist], /) -> HauntTwist: ...


def _rng() -> random.Random:
    return _RNG


class _Recording:
    """Wraps an rng and remembers the last number drawn, for the log line."""

    def __init__(self, rng: _Rng) -> None:
        self._rng = rng
        self.number = -1

    def randrange(self, stop: int, /) -> int:
        self.number = self._rng.randrange(stop)
        return self.number

    def choice(self, seq: Sequence[HauntTwist], /) -> HauntTwist:
        return self._rng.choice(seq)


def roll(run: SeasonRun, *, event_day: bool, rng: _Rng) -> HauntTwist | None:
    """A twist, or None for a clean game. The 0-99 number is always drawn."""
    if run.haunt is None:
        return None
    number = rng.randrange(100)
    all_haunted = event_day and run.event_day is not None and run.event_day.all_haunted
    if all_haunted or number < run.haunt.chance_pct:
        return rng.choice(run.haunt.twists)
    return None


def on_activation(session: Session, game: Game, now: datetime, rng: _Rng | None = None) -> None:
    """Set the game's event-day flag and roll its twist, once, at activation."""
    run = modifiers.season_of(session, game)
    if run is None:
        return
    window = event_day.window_for_game(session, game)
    game.event_day = window is not None and window[0] <= now < window[1]
    if game.event_day and run.event_day is not None:
        logger.info(
            "activated on {event_day_name}",
            event_day_name=run.event_day.names["EN"],
            game_id=game.id,
        )
    if run.haunt is None:
        return
    drawn = _Recording(rng or _rng())
    twist = roll(run, event_day=game.event_day, rng=drawn)
    game.haunt_twist = None if twist is None else twist.value
    if twist is None:
        logger.info(
            "not haunted (rolled {roll}, chance {chance})",
            roll=drawn.number,
            chance=run.haunt.chance_pct,
            event_day=game.event_day,
            game_id=game.id,
        )
    else:
        logger.info(
            "haunted — {twist} (rolled {roll}, chance {chance})",
            twist=twist.value,
            roll=drawn.number,
            chance=run.haunt.chance_pct,
            event_day=game.event_day,
            game_id=game.id,
        )


def stored_twist(game: Game) -> HauntTwist | None:
    if game.haunt_twist is None:
        return None
    try:
        return HauntTwist(game.haunt_twist)
    except ValueError:
        logger.warning(
            "unknown stored twist {twist!r} — rendering the game untwisted",
            twist=game.haunt_twist,
            game_id=game.id,
        )
        return None


def is_haunted(game: Game) -> bool:
    return game.boss_game or stored_twist(game) is not None


def twists_for_stage(run: SeasonRun | None, game: Game, stage: int) -> tuple[HauntTwist, ...]:
    """The twists a stage (1-based) wears, in the order they go on: a boss game
    stacks `BOSS_ORDER` one more per stage, a haunted game has its one stored
    twist, anything else none."""
    if run is None or run.haunt is None:
        return ()
    if game.boss_game:
        return BOSS_ORDER[: min(stage, len(BOSS_ORDER))]
    twist = stored_twist(game)
    return () if twist is None else (twist,)


def fades(game: Game, twist: HauntTwist) -> bool:
    """Whether `twist` clears up on the game's last stage; nothing fades on a boss."""
    return not game.boss_game and twist in FADES_AT_LAST


def _tanuki(image: Image.Image) -> Image.Image:
    hue, sat, val = image.convert("HSV").split()
    hue = hue.point(lambda h: (h + TANUKI_HUE_STEPS) % 256)
    return Image.merge("HSV", (hue, sat, val)).convert("RGB")


def _yurei(image: Image.Image) -> Image.Image:
    dx = round(image.width * YUREI_OFFSET[0])
    dy = round(image.height * YUREI_OFFSET[1])
    return Image.blend(image, ImageChops.offset(image, dx, dy), YUREI_ALPHA)


def _xray(image: Image.Image) -> Image.Image:
    negative = ImageOps.invert(ImageOps.grayscale(image))
    return ImageOps.colorize(negative, XRAY_DARK, XRAY_LIGHT)


_OPS = {
    HauntTwist.KITSUNE_MIRROR: ImageOps.mirror,
    HauntTwist.TANUKI_LEAF: _tanuki,
    HauntTwist.YUREI_DOUBLE: _yurei,
    HauntTwist.KYOKOTSU_XRAY: _xray,
}


def twist_image(image: Image.Image, twist: HauntTwist) -> Image.Image:
    """`image` with the twist on (the last-stage fade is decided by `fades`)."""
    return _OPS[twist](image.convert("RGB"))


def apply_twists(png: bytes, twists: Sequence[HauntTwist]) -> bytes:
    """`png` with every twist applied in order; unchanged when there are none."""
    if not twists:
        return png
    image = Image.open(BytesIO(png))
    for twist in twists:
        image = twist_image(image, twist)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def announcement(session: Session, game: Game, lang: str, number: int, total: int) -> str | None:
    """The haunted-round line for stage `number` of `total`: the start, the
    twist a boss stage adds, or a fading twist lifting on the last stage."""
    run = modifiers.season_of(session, game)
    twists = twists_for_stage(run, game, number)
    if run is None or not twists:
        return None
    twist = twists[-1]
    name = i18n.t(f"haunt.twist.{twist.value}", lang)
    hint = i18n.t(f"haunt.hint.{twist.value}", lang)
    if number == 1:
        # Local import: multipliers imports this module.
        from nani_pix_bot.services.seasons import multipliers

        return i18n.t(
            "haunt.started",
            lang,
            twist=name,
            hint=hint,
            multiplier=multipliers.coin_multiplier(run, game),
        )
    if twists != twists_for_stage(run, game, number - 1):
        return i18n.t("haunt.changed", lang, twist=name, hint=hint)
    if number == total and fades(game, twist):
        return i18n.t("haunt.lifted", lang, twist=name)
    return None


def caption_suffix(session: Session, game: Game, lang: str, number: int, total: int) -> str:
    """`announcement` as a caption tail: a newline and the line, or nothing."""
    line = announcement(session, game, lang, number=number, total=total)
    return "" if line is None else "\n" + line
