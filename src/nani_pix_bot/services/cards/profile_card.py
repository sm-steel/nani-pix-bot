"""The profile card (seasons spec §3): avatar, name, title, achievement chips,
season stamps and a stats line over a banner or the default gradient, with the
glass grid on top. Pure Pillow, no Telegram; the caller localizes every string."""

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

from loguru import logger
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services.cards.glass import gradient, with_grid
from nani_pix_bot.services.cards.icons import ICON_DIR, medal
from nani_pix_bot.services.cards.render import (
    _AVATAR,
    _BACKGROUND,
    _HEIGHT,
    _MARGIN,
    _MUTED,
    _RING_TOTAL,
    _TEXT,
    _WIDTH,
    CARD_SIZE,
    RARITY_COLORS,
    SUPERSAMPLE,
    _cover,
    _fit,
    _name_font,
    _paste_badge,
    _png,
    avatar_disc,
    badge_ring,
    font,
)

PROFILE_STAMPS_MAX = 6
_STAMP_SIZE = 84
_STAMP_GAP = 24
_STAMP_Y = 380
_STAMP_RING = 4
_STAMP_LABEL_SIZES = (20, 18, 16, 14)
_STAMP_LABEL_LINES = 2
PROFILE_BADGES_MAX = 4
_CHIP_Y = 262  # top of the first achievement chip row
_CHIP_ROWS = 2
_CHIP_ROW_GAP = 8
_CHIP_HEIGHT = 44
_CHIP_GAP = 12
_CHIP_PAD = 12
_CHIP_MEDAL = 26
_CHIP_FILL = (12, 13, 20, 150)
_CHIP_LABEL_SIZES = (22, 20, 18)
# The profile's text column, right of the avatar badge; two chips and a gap fit it.
_PROFILE_TEXT_X = 2 * _MARGIN + 2 * (_AVATAR // 2 + _RING_TOTAL)
_PROFILE_TEXT_WIDTH = _WIDTH - _PROFILE_TEXT_X - _MARGIN
_CHIP_MAX_WIDTH = (_PROFILE_TEXT_WIDTH - _CHIP_GAP) // 2
_STATS_Y = _HEIGHT - 70  # vertical centre of the stats line
# The stats are chips like the achievement ones, a little taller, outlined in silver.
_STAT_CHIP_HEIGHT = 52
_STAT_ICON = 32
_STAT_OUTLINE = (190, 190, 205)
_STAT_SIZE = 30
# Every text over the picture gets a thin black outline, readable on any banner.
_OUTLINE_WIDTH = 3
_OUTLINE_FILL = (0, 0, 0)


@dataclass(frozen=True)
class StampEntry:
    label: str
    gold: bool = False
    art: bytes | None = None  # the run's stamp art (PNG); None draws the initials disc


@dataclass(frozen=True)
class StatEntry:
    icon: str  # a PNG's stem in assets/icons/
    text: str


@dataclass(frozen=True)
class BadgeEntry:
    """A shown achievement: its localized name (with the tier) and rarity."""

    label: str
    rarity: Rarity


@dataclass(frozen=True)
class ProfileCard:
    name: str
    title: str | None
    stamps: tuple[StampEntry, ...]
    stats: tuple[StatEntry, ...]
    seed: int
    badges: tuple[BadgeEntry, ...] = ()


def _initials_face(entry: StampEntry, size: int) -> Image.Image:
    seed = int.from_bytes(hashlib.sha256(entry.label.encode()).digest()[:4], "big")
    return avatar_disc(None, entry.label, seed, size)


def _stamp_face(entry: StampEntry, size: int) -> Image.Image:
    """The disc inside the ring: the run's art, centre-cropped to a square and cut to a
    circle, else (no art, or bytes that aren't an image) the initials."""
    if entry.art is None:
        return _initials_face(entry, size)
    try:
        art = Image.open(BytesIO(entry.art)).convert("RGBA")
    except OSError:
        logger.warning(
            "stamp art for {stamp} isn't an image — drawing its initials", stamp=entry.label
        )
        return _initials_face(entry, size)
    face = ImageOps.fit(art, (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    # only the alpha is masked, so the edge keeps the art's colour (no dark fringe)
    face.putalpha(ImageChops.multiply(face.getchannel("A"), mask))
    return face


def _stamp_disc(entry: StampEntry) -> Image.Image:
    """The art (or initials) disc and its ring, drawn at SUPERSAMPLE and shrunk so both edges
    are smooth; the ring is gold when the pass was completed."""
    ring_width = _STAMP_RING * SUPERSAMPLE
    disc_size = _STAMP_SIZE * SUPERSAMPLE
    side = disc_size + 2 * ring_width
    big = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    big.alpha_composite(_stamp_face(entry, disc_size), (ring_width, ring_width))
    ring = RARITY_COLORS[Rarity.GOLD] if entry.gold else _MUTED
    ImageDraw.Draw(big).ellipse((0, 0, side - 1, side - 1), outline=ring, width=ring_width)
    small = side // SUPERSAMPLE
    return big.resize((small, small), Image.Resampling.LANCZOS)


def _label_lines(
    draw: ImageDraw.ImageDraw, text: str, width: int
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """The season name in full: word-wrapped onto at most _STAMP_LABEL_LINES lines,
    at the biggest size where every line fits; never cut short."""
    for size in _STAMP_LABEL_SIZES:
        label_font = font(size)
        lines: list[str] = []
        for word in text.split():
            joined = f"{lines[-1]} {word}" if lines else word
            if lines and draw.textlength(joined, font=label_font) <= width:
                lines[-1] = joined
            else:
                lines.append(word)
        fits = all(draw.textlength(line, font=label_font) <= width for line in lines)
        if fits and len(lines) <= _STAMP_LABEL_LINES:
            return label_font, lines
    return label_font, lines


def _stamp(image: Image.Image, entry: StampEntry, left: int) -> None:
    """One season stamp: the ringed initials disc with the season's full name underneath."""
    disc = _stamp_disc(entry)
    image.paste(disc, (left - _STAMP_RING, _STAMP_Y - _STAMP_RING), disc)
    draw = ImageDraw.Draw(image)
    label_font, lines = _label_lines(draw, entry.label, _STAMP_SIZE + _STAMP_GAP - 4)
    line_height = label_font.size + 2
    for i, line in enumerate(lines):
        draw.text(
            (left + _STAMP_SIZE / 2, _STAMP_Y + _STAMP_SIZE + 22 + i * line_height),
            line,
            font=label_font,
            fill=_MUTED,
            anchor="mm",
            stroke_width=_OUTLINE_WIDTH,
            stroke_fill=_OUTLINE_FILL,
        )


def render_profile_card(
    card: ProfileCard, avatar: bytes | None, background: bytes | None = None
) -> bytes:
    """A player's profile: avatar, name, title, up to PROFILE_BADGES_MAX achievement
    chips, a row of season stamps (at most PROFILE_STAMPS_MAX) and a stats line,
    over the banner (or the default gradient) with the glass grid on top."""
    picture = _cover(background, CARD_SIZE) if background is not None else None
    image = with_grid(gradient(CARD_SIZE) if picture is None else picture)
    draw = ImageDraw.Draw(image)
    color = RARITY_COLORS[Rarity.GOLD]
    draw.rectangle((0, 0, _WIDTH - 1, _HEIGHT - 1), outline=color, width=10)
    badge = badge_ring(
        avatar_disc(avatar, card.name, card.seed, _AVATAR * SUPERSAMPLE), color, Rarity.GOLD
    )
    radius = _AVATAR // 2 + _RING_TOTAL
    _paste_badge(image, badge, (_MARGIN + radius, _HEIGHT // 2))
    x, width = _PROFILE_TEXT_X, _PROFILE_TEXT_WIDTH
    name_font = _name_font(draw, card.name, width)
    name = _fit(draw, card.name, name_font, width)
    draw.text(
        (x, 100),
        name,
        font=name_font,
        fill=_TEXT,
        stroke_width=_OUTLINE_WIDTH,
        stroke_fill=_OUTLINE_FILL,
    )
    if card.title:
        title_font = font(36)
        title = _fit(draw, card.title, title_font, width)
        draw.text(
            (x, 200),
            title,
            font=title_font,
            fill=color,
            stroke_width=_OUTLINE_WIDTH,
            stroke_fill=_OUTLINE_FILL,
        )
    _badges(image, card.badges[:PROFILE_BADGES_MAX], x, width)
    for i, entry in enumerate(card.stamps[:PROFILE_STAMPS_MAX]):
        _stamp(image, entry, x + 4 + i * (_STAMP_SIZE + _STAMP_GAP))
    _stats(image, card.stats, x)
    return _png(image)


def _chip_font(label: str) -> ImageFont.FreeTypeFont:
    """The biggest label size whose chip still fits two to a row."""
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    room = _CHIP_MAX_WIDTH - _CHIP_PAD * 3 - _CHIP_MEDAL
    for size in _CHIP_LABEL_SIZES:
        if measure.textlength(label, font=font(size)) <= room:
            return font(size)
    return font(_CHIP_LABEL_SIZES[-1])


def _badge_chip(entry: BadgeEntry) -> Image.Image:
    """One achievement chip: a rarity medal and the name on a dark translucent pill
    outlined in the rarity colour, drawn at SUPERSAMPLE for smooth corners."""
    label_font = _chip_font(entry.label)
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text_width = round(measure.textlength(entry.label, font=label_font))
    width = _CHIP_PAD * 3 + _CHIP_MEDAL + text_width
    s = SUPERSAMPLE
    big = Image.new("RGBA", (width * s, _CHIP_HEIGHT * s), (0, 0, 0, 0))
    color = RARITY_COLORS[entry.rarity]
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * s - 1, _CHIP_HEIGHT * s - 1),
        radius=_CHIP_HEIGHT * s // 2,
        fill=_CHIP_FILL,
        outline=color,
        width=2 * s,
    )
    chip = big.resize((width, _CHIP_HEIGHT), Image.Resampling.LANCZOS)
    icon = medal(_CHIP_MEDAL, color, _BACKGROUND)
    chip.alpha_composite(icon, (_CHIP_PAD, (_CHIP_HEIGHT - _CHIP_MEDAL) // 2))
    ImageDraw.Draw(chip).text(
        (_CHIP_PAD * 2 + _CHIP_MEDAL, _CHIP_HEIGHT // 2),
        entry.label,
        font=label_font,
        fill=_TEXT,
        anchor="lm",
        stroke_width=_OUTLINE_WIDTH,
        stroke_fill=_OUTLINE_FILL,
    )
    return chip


def _badges(image: Image.Image, badges: tuple[BadgeEntry, ...], left: int, width: int) -> None:
    """The shown achievements, wrapping onto up to _CHIP_ROWS rows; a chip that
    fits on no row is dropped."""
    x, row = left, 0
    for entry in badges:
        chip = _badge_chip(entry)
        if x > left and x + chip.width > left + width:
            x, row = left, row + 1
        if row >= _CHIP_ROWS or chip.width > width:
            break
        image.paste(chip, (x, _CHIP_Y + row * (_CHIP_HEIGHT + _CHIP_ROW_GAP)), chip)
        x += chip.width + _CHIP_GAP


@lru_cache(maxsize=8)
def _icon(key: str) -> Image.Image:
    with Image.open(ICON_DIR / f"{key}.png") as icon:
        return icon.convert("RGBA").resize((_STAT_ICON, _STAT_ICON), Image.Resampling.LANCZOS)


def _stat_chip(entry: StatEntry) -> Image.Image:
    """One counter as a chip: its icon and number on the achievement chips' dark pill,
    outlined in silver, drawn at SUPERSAMPLE for smooth corners."""
    stat_font = font(_STAT_SIZE, bold=True)
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text_width = round(measure.textlength(entry.text, font=stat_font))
    width = _CHIP_PAD * 3 + _STAT_ICON + text_width + 4
    s = SUPERSAMPLE
    big = Image.new("RGBA", (width * s, _STAT_CHIP_HEIGHT * s), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * s - 1, _STAT_CHIP_HEIGHT * s - 1),
        radius=_STAT_CHIP_HEIGHT * s // 2,
        fill=_CHIP_FILL,
        outline=_STAT_OUTLINE,
        width=2 * s,
    )
    chip = big.resize((width, _STAT_CHIP_HEIGHT), Image.Resampling.LANCZOS)
    chip.alpha_composite(_icon(entry.icon), (_CHIP_PAD + 2, (_STAT_CHIP_HEIGHT - _STAT_ICON) // 2))
    ImageDraw.Draw(chip).text(
        (_CHIP_PAD * 2 + _STAT_ICON + 2, _STAT_CHIP_HEIGHT // 2),
        entry.text,
        font=stat_font,
        fill=_TEXT,
        anchor="lm",
    )
    return chip


def _stats(image: Image.Image, stats: tuple[StatEntry, ...], left: int) -> None:
    """The stats line: one chip per counter, left to right, centred on _STATS_Y."""
    for entry in stats:
        chip = _stat_chip(entry)
        image.paste(chip, (left, _STATS_Y - _STAT_CHIP_HEIGHT // 2), chip)
        left += chip.width + _CHIP_GAP
