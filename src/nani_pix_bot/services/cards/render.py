"""Achievement and podium cards (spec §7): pure Pillow, no Telegram. The
caller fetches avatars and localizes every string; this only draws. No
emoji — Pillow can't reliably render colour glyphs — so rarity is a colour
and a drawn medal, and 💠 a drawn diamond."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from types import MappingProxyType

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from nani_pix_bot.models.enums import Rarity

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
_PLACES = (RARITY_COLORS[Rarity.GOLD], RARITY_COLORS[Rarity.SILVER], RARITY_COLORS[Rarity.BRONZE])
_MARGIN = 64
_AVATAR = 300
_RING = 12
_NAME_SIZES = (64, 54, 46, 40)
# Background veil opacities in the card's base colour (spec: readable white text).
_UNLOCK_SHADE = (0.45, 0.88)  # left (behind the avatar) -> right (text area)
_SHADE_RAMP = (400, 640)  # x range where the unlock veil ramps up
_PODIUM_SHADE = (0.60, 0.86)  # overall, title and text bands
_PODIUM_BANDS = ((-40, 130), (440, _HEIGHT + 40))
_BAND_BLUR = 14  # soft band edges
# (index into entries, centre x, avatar size): #1 in the middle and larger.
_PODIUM_SLOTS = ((0, 600, 230), (1, 300, 170), (2, 900, 170))


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
    avatar: bytes | None = None


@dataclass(frozen=True)
class PodiumCard:
    title: str
    entries: tuple[PodiumEntry, ...]


@lru_cache(maxsize=16)
def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
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
        font = _font(size, bold=True)
        if draw.textlength(text, font=font) <= width:
            return font
    return _font(_NAME_SIZES[-1], bold=True)


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
    words = name.lstrip("@").replace("_", " ").split()
    return ("".join(w[0] for w in words[:2]) or "?").upper()


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
        font=_font(size // 3, bold=True),
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


def _ringed(disc: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    size = disc.width + 2 * _RING
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, size - 1, size - 1), fill=color)
    ring.paste(disc, (_RING, _RING), disc)
    return ring


def _diamond(draw: ImageDraw.ImageDraw, center: tuple[float, float], radius: int) -> None:
    x, y = center
    draw.polygon(
        [(x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)], fill=_CURRENCY
    )


def _medal(
    draw: ImageDraw.ImageDraw, center: tuple[float, float], color: tuple[int, int, int]
) -> None:
    x, y = center
    draw.ellipse((x - 18, y - 18, x + 18, y + 18), fill=color)
    draw.ellipse((x - 11, y - 11, x + 11, y + 11), outline=_BACKGROUND, width=3)


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
    draw: ImageDraw.ImageDraw, card: UnlockCard, x: int, color: tuple[int, int, int]
) -> None:
    y = _HEIGHT - 150
    width = _WIDTH - x - _MARGIN
    handle_font = _font(38, bold=True)
    draw.text((x, y), _fit(draw, card.handle, handle_font, width), font=handle_font, fill=_TEXT)
    y += 62
    label_font = _font(32)
    _medal(draw, (x + 18, y + 22), color)
    draw.text((x + 48, y), card.rarity_label, font=label_font, fill=_TEXT)
    reward_x = x + 48 + draw.textlength(card.rarity_label, font=label_font) + 40
    _diamond(draw, (reward_x + 16, y + 22), 16)
    reward = f"+{card.reward}   {card.points_label}"
    draw.text((reward_x + 44, y), reward, font=_font(32, bold=True), fill=_TEXT)


def render_unlock_card(
    card: UnlockCard, avatar: bytes | None, background: bytes | None = None
) -> bytes:
    image = _canvas(background, _unlock_shade())
    draw = ImageDraw.Draw(image)
    color = RARITY_COLORS[card.rarity]
    draw.rectangle((0, 0, _WIDTH - 1, _HEIGHT - 1), outline=color, width=10)
    disc = _ringed(avatar_disc(avatar, card.handle, card.seed, _AVATAR), color)
    image.paste(disc, (_MARGIN, (_HEIGHT - disc.height) // 2), disc)
    x = 2 * _MARGIN + disc.width
    width = _WIDTH - x - _MARGIN
    headline_font, body_font = _font(30, bold=True), _font(34)
    name_font = _name_font(draw, card.name, width)
    headline = _fit(draw, card.headline.upper(), headline_font, width)
    draw.text((x, 100), headline, font=headline_font, fill=color)
    draw.text((x, 150), _fit(draw, card.name, name_font, width), font=name_font, fill=_TEXT)
    for i, line in enumerate(_wrap(draw, card.description, body_font, width)):
        draw.text((x, 250 + i * 46), line, font=body_font, fill=_MUTED)
    _unlock_footer(draw, card, x, color)
    return _png(image)


def _podium_entry(image: Image.Image, entry: PodiumEntry, slot: tuple[int, int, int]) -> None:
    index, centre_x, size = slot
    draw = ImageDraw.Draw(image)
    disc = _ringed(avatar_disc(entry.avatar, entry.name, entry.seed, size), _PLACES[index])
    top = 300 - disc.height // 2 + (0 if index == 0 else 30)
    image.paste(disc, (centre_x - disc.width // 2, top), disc)
    text_y = top + disc.height + 40
    name_font = _font(36 if index == 0 else 30, bold=True)
    name = _fit(draw, entry.name, name_font, 280)
    draw.text((centre_x, text_y), name, font=name_font, fill=_TEXT, anchor="mm")
    draw.text((centre_x, text_y + 44), entry.score_label, font=_font(28), fill=_MUTED, anchor="mm")


def render_podium_card(card: PodiumCard, background: bytes | None = None) -> bytes:
    image = _canvas(background, _podium_shade())
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, _WIDTH - 1, _HEIGHT - 1), outline=_PLACES[0], width=10)
    title_font = _font(52, bold=True)
    title = _fit(draw, card.title, title_font, _WIDTH - 2 * _MARGIN)
    draw.text((_WIDTH / 2, 70), title, font=title_font, fill=_TEXT, anchor="mm")
    for slot in _PODIUM_SLOTS:
        if slot[0] < len(card.entries):
            _podium_entry(image, card.entries[slot[0]], slot)
    return _png(image)
