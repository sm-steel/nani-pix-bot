"""Achievement and podium cards (spec §7): pure Pillow, no Telegram. The
caller fetches avatars and localizes every string; this only draws. No
emoji — Pillow can't reliably render colour glyphs — so rarity is a colour
and a drawn medal, and 💠 a drawn diamond."""

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from types import MappingProxyType

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services.cards.icons import diamond, medal, star, trophy

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
CARD_SIZE = (1200, 630)
_WIDTH, _HEIGHT = CARD_SIZE
_BACKGROUND = (22, 24, 31)
_TEXT = (241, 242, 246)
_MUTED = (160, 166, 180)
_CURRENCY = (110, 190, 255)
RARITY_COLORS: Mapping[Rarity, tuple[int, int, int]] = MappingProxyType(
    {
        Rarity.BRONZE: (205, 127, 50),
        Rarity.SILVER: (192, 199, 212),
        Rarity.GOLD: (240, 190, 40),
        Rarity.PLATINUM: (110, 225, 235),
    }
)
_PLACES = tuple(RARITY_COLORS[r] for r in (Rarity.GOLD, Rarity.SILVER, Rarity.BRONZE))
_MARGIN = 64
_AVATAR = 300
# The avatar badge: photo, solid inner ring, gradient outer ring, soft glow.
_INNER_RING = 6
_OUTER_RING = 8
_RING_TOTAL = _INNER_RING + _OUTER_RING
_GLOW_BLUR = 12
_GLOW_ALPHA = 0.55
_GLOW_PAD = 3 * _GLOW_BLUR  # room for the blur so the glow isn't clipped
SUPERSAMPLE = 4  # supersampling factor: the badge is drawn big, then shrunk with LANCZOS
_SWEEP_STEPS = 720  # wedges per revolution when drawing the sweep gradient
_GLINTS = (225.0, 45.0)  # outer-ring highlights, in PIL angles (top-left, bottom-right)
_GLINT_WIDTH = 24.0  # degrees either side of a glint's centre
_GLINT_STRENGTH = 0.8
_GLINT_COLOR = (252, 252, 255)
# Outer-ring sweep palettes: related to the rarity colour, clearly different from it.
# Each is interpolated around the full circle and wraps back to its first stop.
SWEEP: Mapping[Rarity, tuple[tuple[int, int, int], ...]] = MappingProxyType(
    {
        Rarity.BRONZE: ((0x8E, 0x3B, 0x24), (0xF2, 0xA9, 0x7A), (0xC9, 0x87, 0x7A)),
        Rarity.SILVER: ((0x6F, 0x86, 0xA8), (0xB9, 0xB2, 0xE0), (0xEE, 0xF1, 0xF8)),
        Rarity.GOLD: ((0xE0, 0x84, 0x1A), (0xFF, 0xE9, 0xA8), (0xF5, 0xC4, 0x51)),
        Rarity.PLATINUM: ((0x8C, 0x6B, 0xE8), (0xE8, 0xFB, 0xFF), (0x2F, 0xBF, 0xC4)),
    }
)
# Podium places wear the matching rarity's ring: #1 gold, #2 silver, #3 bronze.
_PLACE_RARITIES = (Rarity.GOLD, Rarity.SILVER, Rarity.BRONZE)
_NAME_SIZES = (64, 54, 46, 40)
_STAR_BOX = 44  # the star icon incl. its halo
_STAR_ADVANCE = 34  # from the star's left edge to the score text
# Background veil opacities in the card's base colour (spec: readable white text).
_UNLOCK_SHADE = (0.45, 0.88)  # left (behind the avatar) -> right (text area)
_SHADE_RAMP = (400, 640)  # x range where the unlock veil ramps up
_PODIUM_SHADE = (0.60, 0.86)  # overall, title and text bands
_PODIUM_BANDS = ((-40, 130), (440, _HEIGHT + 40))
_BAND_BLUR = 14  # soft band edges
# Avatar sizes: #1 (every co-champion) larger than #2 and #3.
_CHAMPION_SIZE = 230
_PLACED_SIZE = 170
_PODIUM_NAME_WIDTH = 280
_BADGE_GAP = 12  # at least this much clear space between two badges
# The most players the card draws; the summary text lists everyone placed.
PODIUM_CARD_MAX = 4


@dataclass(frozen=True)
class UnlockCard:
    headline: str
    name: str
    description: str
    handle: str
    reward: int
    points_label: str
    rarity_label: str
    rarity: Rarity
    seed: int


@dataclass(frozen=True)
class PodiumEntry:
    name: str
    score_label: str
    seed: int
    rank: int  # the competition rank: co-champions all hold 1
    avatar: bytes | None = None


@dataclass(frozen=True)
class PodiumSlot:
    entry: int  # index into the card's entries
    place: int  # rank - 1: the ring colour and the champion-or-not styling
    x: int  # centre
    size: int  # avatar diameter
    name_width: int


# The plain 1/2/3 podium: #1 in the middle and larger, #2 left, #3 right.
_CLASSIC_X = (600, 300, 900)


@dataclass(frozen=True)
class PodiumCard:
    title: str
    entries: tuple[PodiumEntry, ...]


@lru_cache(maxsize=16)
def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _name_font(draw: ImageDraw.ImageDraw, text: str, width: int) -> ImageFont.FreeTypeFont:
    """The biggest title size that fits on one line; the smallest one is cut instead."""
    for size in _NAME_SIZES[:-1]:
        candidate = font(size, bold=True)
        if draw.textlength(text, font=candidate) <= width:
            return candidate
    return font(_NAME_SIZES[-1], bold=True)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    """Up to two lines; the second one is cut with an ellipsis."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
            continue
        lines.append(current)
        current = word
    lines.append(current)
    if len(lines) <= 2:
        return [_fit(draw, line, font, width) for line in lines]
    return [_fit(draw, lines[0], font, width), _fit(draw, " ".join(lines[1:]), font, width)]


def _seed_color(seed: int) -> tuple[int, int, int]:
    digest = hashlib.sha256(str(seed).encode()).digest()
    return (70 + digest[0] % 120, 70 + digest[1] % 120, 70 + digest[2] % 120)


def _initials(name: str) -> str:
    """Up to two initials, letters only: "@kurogane_42" -> "K", "@Sakura_chan" -> "SC"."""
    words = re.split(r"[\W_]+", name)
    letters = [next((c for c in word if c.isalpha()), "") for word in words]
    return ("".join(c for c in letters if c)[:2] or "?").upper()


def _photo(avatar: bytes | None, size: int) -> Image.Image | None:
    if avatar is None:
        return None
    try:
        image = Image.open(BytesIO(avatar)).convert("RGB")
    except OSError:
        return None
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    square = image.crop((left, top, left + side, top + side))
    return square.resize((size, size), Image.Resampling.LANCZOS)


def _initials_face(name: str, seed: int, size: int) -> Image.Image:
    face = Image.new("RGB", (size, size), _seed_color(seed))
    ImageDraw.Draw(face).text(
        (size / 2, size / 2),
        _initials(name),
        font=font(size // 3, bold=True),
        fill=_TEXT,
        anchor="mm",
    )
    return face


def avatar_disc(avatar: bytes | None, name: str, seed: int, size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    face = _photo(avatar, size) or _initials_face(name, seed, size)
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    disc.paste(face, (0, 0), mask)
    return disc


def _blend(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, int, int]:
    r, g, bl = (round(x + (y - x) * t) for x, y in zip(a, b, strict=True))
    return (r, g, bl)


def _sweep_color(palette: tuple[tuple[int, int, int], ...], angle: float) -> tuple[int, int, int]:
    """The ring colour at `angle` degrees: the palette's stops, smoothly
    interpolated around the circle, plus a soft white glint at each _GLINTS."""
    position = (angle % 360) / 360 * len(palette)
    index = int(position)
    t = position - index
    color = _blend(palette[index], palette[(index + 1) % len(palette)], t * t * (3 - 2 * t))
    for glint in _GLINTS:
        distance = abs((angle - glint + 180) % 360 - 180)
        weight = max(0.0, 1 - distance / _GLINT_WIDTH)
        color = _blend(color, _GLINT_COLOR, _GLINT_STRENGTH * weight * weight * (3 - 2 * weight))
    return color


def sweep(
    size: int, inner: float, outer: float, palette: tuple[tuple[int, int, int], ...]
) -> Image.Image:
    """A conic gradient over the ring between `inner` and `outer`, drawn as thin
    overlapping quads (the annulus mask trims the slack)."""
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    centre = size / 2
    near, far = inner - 2, outer + 2
    step = 360 / _SWEEP_STEPS
    for i in range(_SWEEP_STEPS):
        start = i * step
        rays = [math.radians(start), math.radians(start + step * 1.5)]
        quad = [
            (centre + r * math.cos(a), centre + r * math.sin(a)) for r in (near, far) for a in rays
        ]
        draw.polygon(
            [quad[0], quad[1], quad[3], quad[2]], fill=_sweep_color(palette, start + step / 2)
        )
    return image


def _disc_mask(size: int, centre: float, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    box = (centre - radius, centre - radius, centre + radius, centre + radius)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask


def _glow(size: int, radius: float, color: tuple[int, int, int]) -> Image.Image:
    """The outer ring's silhouette in the badge colour, blurred, behind the badge."""
    alpha = _disc_mask(size, size / 2, radius).filter(ImageFilter.GaussianBlur(_GLOW_BLUR))
    layer = Image.new("RGBA", (size, size), color)
    layer.putalpha(alpha.point(lambda v: round(v * _GLOW_ALPHA)))
    return layer


def badge_ring(face: Image.Image, color: tuple[int, int, int], rarity: Rarity) -> Image.Image:
    """The avatar badge, centred in a transparent square with room for its glow.
    `color` (rarity or place) sets the inner ring and glow; `rarity` picks the outer
    ring's sweep palette. `face` is the avatar disc drawn at SUPERSAMPLE times its final size;
    everything is drawn at that scale and shrunk once, so no edge is aliased."""
    face_radius = face.width / 2
    inner = face_radius + _INNER_RING * SUPERSAMPLE
    outer = inner + _OUTER_RING * SUPERSAMPLE
    big = face.width + 2 * (_RING_TOTAL + _GLOW_PAD) * SUPERSAMPLE
    centre = big / 2
    badge = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    annulus = _disc_mask(big, centre, outer)
    annulus.paste(0, mask=_disc_mask(big, centre, inner))
    badge.paste(sweep(big, inner, outer, SWEEP[rarity]), (0, 0), annulus)
    badge.paste(Image.new("RGB", (big, big), color), (0, 0), _disc_mask(big, centre, inner))
    corner = round(centre - face_radius)
    badge.paste(face, (corner, corner), face)
    final = big // SUPERSAMPLE
    badge = badge.resize((final, final), Image.Resampling.LANCZOS)
    glow = _glow(final, face_radius / SUPERSAMPLE + _RING_TOTAL, color)
    return Image.alpha_composite(glow, badge)


def _paste_badge(image: Image.Image, badge: Image.Image, centre: tuple[int, int]) -> None:
    half = badge.width // 2
    image.paste(badge, (centre[0] - half, centre[1] - half), badge)


def _cover(background: bytes, size: tuple[int, int]) -> Image.Image | None:
    """Scale to fill and centre-crop; None if the bytes aren't an image."""
    try:
        image = Image.open(BytesIO(background)).convert("RGB")
    except OSError:
        return None
    scale = max(size[0] / image.width, size[1] / image.height)
    scaled = image.resize(
        (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
    )
    left, top = (scaled.width - size[0]) // 2, (scaled.height - size[1]) // 2
    return scaled.crop((left, top, left + size[0], top + size[1]))


def _canvas(background: bytes | None, shade: Image.Image) -> Image.Image:
    """The flat card, or the background darkened by `shade` (an L mask, 255 = opaque)."""
    flat = Image.new("RGB", CARD_SIZE, _BACKGROUND)
    picture = _cover(background, CARD_SIZE) if background is not None else None
    if picture is None:
        return flat
    return Image.composite(flat, picture, shade)


def _unlock_shade() -> Image.Image:
    """Light behind the avatar, ramping up to the text area on the right."""
    row = Image.new("L", (_WIDTH, 1))
    for x in range(_WIDTH):
        t = min(1.0, max(0.0, (x - _SHADE_RAMP[0]) / (_SHADE_RAMP[1] - _SHADE_RAMP[0])))
        alpha = _UNLOCK_SHADE[0] + (_UNLOCK_SHADE[1] - _UNLOCK_SHADE[0]) * t
        row.putpixel((x, 0), round(alpha * 255))
    return row.resize(CARD_SIZE)


def _podium_shade() -> Image.Image:
    """An even veil, stronger behind the title and the names and scores."""
    shade = Image.new("L", CARD_SIZE, round(_PODIUM_SHADE[0] * 255))
    strong = round(_PODIUM_SHADE[1] * 255)
    draw = ImageDraw.Draw(shade)
    for top, bottom in _PODIUM_BANDS:
        draw.rectangle((0, top, _WIDTH, bottom), fill=strong)
    return shade.filter(ImageFilter.GaussianBlur(_BAND_BLUR))


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _unlock_footer(
    image: Image.Image, card: UnlockCard, x: int, color: tuple[int, int, int]
) -> None:
    draw = ImageDraw.Draw(image)
    y = _HEIGHT - 150
    width = _WIDTH - x - _MARGIN
    handle_font = font(38, bold=True)
    draw.text((x, y), _fit(draw, card.handle, handle_font, width), font=handle_font, fill=_TEXT)
    y += 62
    label_font = font(32)
    badge = medal(36, color, _BACKGROUND)
    image.paste(badge, (x, y + 4), badge)
    draw.text((x + 48, y), card.rarity_label, font=label_font, fill=_TEXT)
    reward_x = x + 48 + draw.textlength(card.rarity_label, font=label_font) + 40
    gem = diamond(32, _CURRENCY)
    image.paste(gem, (round(reward_x), y + 6), gem)
    bold = font(32, bold=True)
    reward = f"+{card.reward}"
    draw.text((reward_x + 44, y), reward, font=bold, fill=_TEXT)
    points_x = reward_x + 44 + draw.textlength(reward, font=bold) + 36
    icon = trophy(32)
    image.paste(icon, (round(points_x), y + 22 - 16), icon)
    draw.text((points_x + 44, y), card.points_label, font=bold, fill=_TEXT)


def render_unlock_card(
    card: UnlockCard, avatar: bytes | None, background: bytes | None = None
) -> bytes:
    image = _canvas(background, _unlock_shade())
    draw = ImageDraw.Draw(image)
    color = RARITY_COLORS[card.rarity]
    draw.rectangle((0, 0, _WIDTH - 1, _HEIGHT - 1), outline=color, width=10)
    badge = badge_ring(
        avatar_disc(avatar, card.handle, card.seed, _AVATAR * SUPERSAMPLE), color, card.rarity
    )
    radius = _AVATAR // 2 + _RING_TOTAL
    _paste_badge(image, badge, (_MARGIN + radius, _HEIGHT // 2))
    x = 2 * _MARGIN + 2 * radius
    width = _WIDTH - x - _MARGIN
    headline_font, body_font = font(30, bold=True), font(34)
    name_font = _name_font(draw, card.name, width)
    headline = _fit(draw, card.headline.upper(), headline_font, width)
    draw.text((x, 100), headline, font=headline_font, fill=color)
    draw.text((x, 150), _fit(draw, card.name, name_font, width), font=name_font, fill=_TEXT)
    for i, line in enumerate(_wrap(draw, card.description, body_font, width)):
        draw.text((x, 250 + i * 46), line, font=body_font, fill=_MUTED)
    _unlock_footer(image, card, x, color)
    return _png(image)


def _tier_size(place: int) -> int:
    return _CHAMPION_SIZE if place == 0 else _PLACED_SIZE


def _classic_slots(count: int) -> tuple[PodiumSlot, ...]:
    return tuple(
        PodiumSlot(i, i, _CLASSIC_X[i], _tier_size(i), _PODIUM_NAME_WIDTH) for i in range(count)
    )


def _row_order(ranks: tuple[int, ...]) -> list[int]:
    """Entry indexes left to right: the co-champions in the middle, the
    others alternating left and right of them (the first one left, like #2)."""
    champions = [i for i, rank in enumerate(ranks) if rank == 1]
    others = [i for i, rank in enumerate(ranks) if rank != 1]
    return others[0::2][::-1] + champions + others[1::2]


def _shown(ranks: tuple[int, ...]) -> list[int]:
    """The entries the card has room for: the best ranks first (so every
    co-champion), then stored order; kept in stored order."""
    best = sorted(range(len(ranks)), key=lambda i: (ranks[i], i))
    return sorted(best[:PODIUM_CARD_MAX])


def podium_slots(ranks: tuple[int, ...]) -> tuple[PodiumSlot, ...]:
    """Where each entry sits, for at most PODIUM_CARD_MAX of them (see
    _shown), so the badges never shrink below the four-entry sizes."""
    shown = _shown(ranks)
    slots = _row_slots(tuple(ranks[i] for i in shown))
    return tuple(replace(slot, entry=shown[slot.entry]) for slot in slots)


def _row_slots(ranks: tuple[int, ...]) -> tuple[PodiumSlot, ...]:
    """By rank rather than list position. A plain 1/2/3 podium (or its
    first one or two places) keeps the classic layout; shared ranks spread
    every entry evenly in one row, champions large in the middle, shrunk
    only as far as needed to keep the badges apart."""
    if ranks == tuple(range(1, len(ranks) + 1)) and len(ranks) <= len(_CLASSIC_X):
        return _classic_slots(len(ranks))
    spacing = _WIDTH / (len(ranks) + 1)
    fit = int(spacing) - 2 * _RING_TOTAL - _BADGE_GAP
    name_width = min(_PODIUM_NAME_WIDTH, int(spacing) - 20)
    slots = [
        PodiumSlot(
            entry=entry,
            place=ranks[entry] - 1,
            x=round((column + 1) * spacing),
            size=min(_tier_size(ranks[entry] - 1), fit),
            name_width=name_width,
        )
        for column, entry in enumerate(_row_order(ranks))
    ]
    return tuple(sorted(slots, key=lambda slot: slot.entry))


def _podium_entry(image: Image.Image, entry: PodiumEntry, slot: PodiumSlot) -> None:
    draw = ImageDraw.Draw(image)
    face = avatar_disc(entry.avatar, entry.name, entry.seed, slot.size * SUPERSAMPLE)
    champion = slot.place == 0
    centre_y = 300 + (0 if champion else 30)
    ring = badge_ring(face, _PLACES[slot.place], _PLACE_RARITIES[slot.place])
    _paste_badge(image, ring, (slot.x, centre_y))
    text_y = centre_y + slot.size // 2 + _RING_TOTAL + 40
    name_font = font(36 if champion else 30, bold=True)
    name = _fit(draw, entry.name, name_font, slot.name_width)
    draw.text((slot.x, text_y), name, font=name_font, fill=_TEXT, anchor="mm")
    _score_line(image, entry.score_label, (slot.x, text_y + 44))


def _score_line(image: Image.Image, label: str, centre: tuple[int, int]) -> None:
    """A glowing star, then the score, centred together on `centre`."""
    draw = ImageDraw.Draw(image)
    star_font = font(28)
    left = centre[0] - (_STAR_ADVANCE + draw.textlength(label, font=star_font)) / 2
    icon = star(_STAR_BOX)
    image.paste(icon, (round(left + 14 - _STAR_BOX / 2), centre[1] - _STAR_BOX // 2), icon)
    draw.text((left + _STAR_ADVANCE, centre[1]), label, font=star_font, fill=_MUTED, anchor="lm")


def render_podium_card(card: PodiumCard, background: bytes | None = None) -> bytes:
    image = _canvas(background, _podium_shade())
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, _WIDTH - 1, _HEIGHT - 1), outline=_PLACES[0], width=10)
    title_font = font(52, bold=True)
    title = _fit(draw, card.title, title_font, _WIDTH - 2 * _MARGIN)
    draw.text((_WIDTH / 2, 70), title, font=title_font, fill=_TEXT, anchor="mm")
    for slot in podium_slots(tuple(entry.rank for entry in card.entries)):
        _podium_entry(image, card.entries[slot.entry], slot)
    return _png(image)
