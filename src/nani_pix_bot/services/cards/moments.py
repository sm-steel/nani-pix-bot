"""Moment cards for the game topic (Halloween): a player's profile photo in a glowing
ring beside the item's art, a title pill on top and name pills below, over a generated
background. Pure Pillow, no Telegram: the caller fetches avatars and localizes the text."""

import enum
import math
import unicodedata
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from nani_pix_bot.services.cards.render import avatar_disc, font
from nani_pix_bot.services.cosmetics import card_style

MOMENT_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "moments"
WIDTH, HEIGHT = 1200, 800
_JPEG_QUALITY = 90
_HALO_PAD = 14
_HALO_ALPHA = 200
_HALO_BLUR = 22
_RING_WIDTH = 8
_TITLE_PILL = card_style.PillSize(height=61, radius=30, font_size=38, padding=53)
_NAME_PILL = card_style.PillSize(height=54, radius=27, font_size=34, padding=48)
_SMALL_PILL = card_style.PillSize(height=42, radius=21, font_size=26, padding=36)
_TITLE_WIDTH = 1040
_NAME_WIDTH = 360
_SMALL_NAME_WIDTH = 300
_FADED_ALPHA = 0.55
_CHARM_ART_BOX = (360, 420)
_CHARM_ART_X = 790
_CHARM_CLEAR_X = 480 + 170 + _HALO_PAD + 24  # the avatar disc's halo, plus a gap
_DUEL_ART_BOX = (300, 300)
_ATTACK_ART_BOX = (230, 230)
_DEFENCE_ART_BOX = (330, 330)
_TRAIL_SPACING = 28
_TRAIL_DOT = 8
_PILL_REACH = 400  # half the span a pill is centred in; wider than any pill


class Mood(enum.StrEnum):
    CHARM = "charm"  # warm shrine background
    CURSE = "curse"  # violet mist background


RING: dict[Mood, tuple[int, int, int]] = {
    Mood.CHARM: (255, 170, 60),
    Mood.CURSE: (180, 110, 255),
}


@dataclass(frozen=True)
class Face:
    avatar: bytes | None
    name: str
    seed: int  # player id, for the initials fallback colour


@cache
def _background(mood: Mood) -> Image.Image:
    with Image.open(MOMENT_ASSETS / f"{mood.value}_background.jpg") as source:
        return ImageOps.fit(source.convert("RGBA"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def _plain(text: str) -> str:
    """`text` without emoji and other symbols: the card font has none of them."""
    kept = "".join(
        c
        for c in text
        if unicodedata.category(c) not in {"So", "Cs", "Cf", "Mn", "Sk"} and ord(c) < 0x1F000
    )
    return " ".join(kept.split()) or text.strip()


def _clip(text: str, size: int, width: int) -> str:
    """`text` cut with an ellipsis so it fits `width` at `size` in the pill font."""
    label_font = font(size, bold=True)
    if label_font.getlength(text) <= width:
        return text
    while text and label_font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _pill(
    canvas: Image.Image,
    text: str,
    size: card_style.PillSize,
    centre: tuple[int, int],
    width: int,
) -> None:
    """`text` in a pill centred on `centre`, clipped to `width`; nothing for an empty text."""
    if not text.strip():
        return
    label = _clip(_plain(text), size.font_size, width)
    x, y = centre
    card_style.draw_pill(
        canvas, label, size, (x - _PILL_REACH, x + _PILL_REACH, y - size.height // 2)
    )


def _glow_disc(
    canvas: Image.Image,
    face: Face,
    size: int,
    centre: tuple[int, int],
    colour: tuple[int, int, int],
) -> None:
    """The avatar disc with a blurred halo and a ring in the mood colour."""
    x, y = centre
    radius = size / 2 + _HALO_PAD
    halo = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse(
        (x - radius, y - radius, x + radius, y + radius), fill=(*colour, _HALO_ALPHA)
    )
    canvas.alpha_composite(halo.filter(ImageFilter.GaussianBlur(_HALO_BLUR)))
    ring = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse(
        (x - radius + 4, y - radius + 4, x + radius - 4, y + radius - 4),
        outline=(*colour, 255),
        width=_RING_WIDTH,
    )
    canvas.alpha_composite(ring)
    canvas.alpha_composite(
        avatar_disc(face.avatar, _plain(face.name), face.seed, size),
        (round(x - size / 2), round(y - size / 2)),
    )


def _art(
    image: Image.Image, box: tuple[int, int], angle: float = 0, alpha: float = 1.0
) -> Image.Image:
    """`image` trimmed to what is drawn and scaled to fit inside `box` (width, height),
    keeping its aspect, then turned by `angle` degrees (counter-clockwise)."""
    picture = image.convert("RGBA")
    bounds = picture.getchannel("A").getbbox()
    if bounds is not None:
        picture = picture.crop(bounds)
    scale = min(box[0] / picture.width, box[1] / picture.height)
    size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
    picture = picture.resize(size, Image.Resampling.LANCZOS)
    if angle:
        picture = picture.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    if alpha < 1.0:
        picture.putalpha(picture.getchannel("A").point(lambda a: round(a * alpha)))
    return picture


def _paste(canvas: Image.Image, picture: Image.Image, centre: tuple[int, int]) -> None:
    canvas.alpha_composite(
        picture, (round(centre[0] - picture.width / 2), round(centre[1] - picture.height / 2))
    )


def _jpeg(canvas: Image.Image) -> bytes:
    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()


def _title(canvas: Image.Image, title: str) -> None:
    _pill(canvas, title, _TITLE_PILL, (WIDTH // 2, 70), _TITLE_WIDTH)


def charm_card(mood: Mood, face: Face, art: Image.Image, title: str) -> bytes:
    """One player and the item they used: avatar left, tilted art right."""
    canvas = _background(mood).copy()
    _title(canvas, title)
    _glow_disc(canvas, face, 340, (480, 420), RING[mood])
    picture = _art(art, _CHARM_ART_BOX, angle=-12)
    # a wide item (the ticket) is pushed right so it never reaches the avatar's ring
    x = max(_CHARM_ART_X, _CHARM_CLEAR_X + picture.width // 2)
    _paste(canvas, picture, (x, 420))
    _pill(canvas, face.name, _NAME_PILL, (480, 650), _NAME_WIDTH)
    return _jpeg(canvas)


def _spirit_trail(
    canvas: Image.Image,
    start: tuple[int, int],
    end: tuple[int, int],
    colour: tuple[int, int, int],
) -> None:
    """A trail of solid dots from `start` to `end`, a soft glow of the same dots under them."""
    length = math.dist(start, end)
    count = max(2, round(length / _TRAIL_SPACING) + 1)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i in range(count):
        t = i / (count - 1)
        x = start[0] + (end[0] - start[0]) * t
        y = start[1] + (end[1] - start[1]) * t
        draw.ellipse(
            (x - _TRAIL_DOT, y - _TRAIL_DOT, x + _TRAIL_DOT, y + _TRAIL_DOT), fill=(*colour, 255)
        )
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(9)))
    canvas.alpha_composite(layer)


def _crack(canvas: Image.Image, centre: tuple[int, int], height: int) -> None:
    """A jagged crack line across the faded attacking art."""
    x, y = centre
    top, bottom = y - height // 2 - 10, y + height // 2 + 10
    points = [
        (x - 12, top),
        (x + 14, y - height // 4),
        (x - 10, y - 6),
        (x + 16, y + height // 4),
        (x - 6, bottom),
    ]
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).line(points, fill=(255, 255, 255, 235), width=5, joint="curve")
    canvas.alpha_composite(layer)


@dataclass(frozen=True)
class Duel:
    actor: Face  # who cast
    target: Face  # who it was cast at


def blocked_duel_card(
    mood: Mood, duel: Duel, attack: Image.Image, defence: Image.Image, title: str
) -> bytes:
    """A hex blocked: the target in the middle beside their `defence` art, while the
    `attack` art lies faded and cracked next to the small caster's name."""
    canvas = _background(mood).copy()
    _title(canvas, title)
    _paste(canvas, _art(attack, _ATTACK_ART_BOX, angle=18, alpha=_FADED_ALPHA), (230, 300))
    _crack(canvas, (230, 300), 230)
    _pill(canvas, duel.actor.name, _SMALL_PILL, (230, 470), _SMALL_NAME_WIDTH)
    _glow_disc(canvas, duel.target, 320, (560, 420), RING[mood])
    _paste(canvas, _art(defence, _DEFENCE_ART_BOX), (860, 470))
    _pill(canvas, duel.target.name, _NAME_PILL, (560, 640), _NAME_WIDTH)
    return _jpeg(canvas)


def duel_card(mood: Mood, duel: Duel, art: Image.Image, title: str, blocked: bool = False) -> bytes:
    """Caster, art, target in a row (a hex being cast). With `blocked`, the blocked
    layout using `art` for both sides; `blocked_duel_card` takes one art each."""
    if blocked:
        return blocked_duel_card(mood, duel, art, art, title)
    canvas = _background(mood).copy()
    colour = RING[mood]
    _title(canvas, title)
    _spirit_trail(canvas, (349, 430), (736, 420), colour)
    _glow_disc(canvas, duel.actor, 210, (230, 430), colour)
    _paste(canvas, _art(art, _DUEL_ART_BOX), (560, 400))
    _glow_disc(canvas, duel.target, 300, (900, 420), colour)
    _pill(canvas, duel.actor.name, _SMALL_PILL, (230, 590), _SMALL_NAME_WIDTH)
    _pill(canvas, duel.target.name, _NAME_PILL, (900, 630), _NAME_WIDTH)
    return _jpeg(canvas)
