"""Moment cards for the game topic (Halloween): a player's profile photo in a glowing
ring beside the item's art, a title pill on top and name pills below, over a generated
background. Pure Pillow, no Telegram: the caller fetches avatars and localizes the text."""

import enum
import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from nani_pix_bot.services.cards.render import SUPERSAMPLE, avatar_disc, font, smooth_mask
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
_FADED_ALPHA = 0.6
_FADED_COLOUR = 0.4  # ImageEnhance.Color factor: 60% desaturated
_CUT_WIDTH = 16  # the torn bands of the cross
_CUT_JAG = 4
_CUT_PUSH = 10  # how far each of the four pieces moves outward
_SPARKLE = (255, 215, 90)
# golden stars in the gap, as offsets from the art's centre: one in the middle, three on the arms
_SPARKLES = (((0, 0), 14), ((52, 52), 12), ((-52, 52), 15), ((52, -52), 11))
_CHARM_ART_BOX = (360, 420)
_CHARM_ART_X = 790
_CHARM_CLEAR_X = 480 + 170 + _HALO_PAD + 24  # the avatar disc's halo, plus a gap
_DUEL_ART_BOX = (300, 300)
_ATTACK_ART_BOX = (230, 230)
_DEFENCE_ART_BOX = (330, 330)
_TRAIL_SPACING = 28
_TRAIL_HEIGHT = 20  # a chevron's arm-to-arm span
_TRAIL_DEPTH = 10  # tip to the arms' ends, along the trail
_TRAIL_STROKE = 6
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


def _paint(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    colour: tuple[int, int, int, int],
    shape: Callable[[ImageDraw.ImageDraw, int], None],
    blur: float = 0.0,
) -> None:
    """`shape` (drawn in the box's own coordinates, at 4x) in `colour` over `box`,
    antialiased, optionally blurred; the box must lie inside the canvas."""
    x0, y0, x1, y1 = box
    mask = smooth_mask((x1 - x0, y1 - y0), shape)
    if blur:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    layer = Image.new("RGBA", mask.size, (*colour[:3], 0))
    layer.putalpha(mask.point(lambda v: v * colour[3] // 255))
    canvas.alpha_composite(layer, (x0, y0))


def _face_disc(face: Face, size: int) -> Image.Image:
    """The avatar disc drawn at SUPERSAMPLE times the size and shrunk once, so its edge
    is antialiased (alpha only: the colour at the edge stays the avatar's)."""
    big = avatar_disc(face.avatar, _plain(face.name), face.seed, size * SUPERSAMPLE)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def _glow_disc(
    canvas: Image.Image,
    face: Face,
    size: int,
    centre: tuple[int, int],
    colour: tuple[int, int, int],
) -> None:
    """The avatar disc with a blurred halo and a ring in the mood colour."""
    x, y = centre
    radius = size // 2 + _HALO_PAD
    reach = radius + 3 * _HALO_BLUR
    box = (x - reach, y - reach, x + reach, y + reach)

    def disc(draw: ImageDraw.ImageDraw, k: int, r: int, fill: int) -> None:
        mid = reach * k
        draw.ellipse((mid - r * k, mid - r * k, mid + r * k, mid + r * k), fill=fill)

    def halo(draw: ImageDraw.ImageDraw, k: int) -> None:
        disc(draw, k, radius, 255)

    def ring(draw: ImageDraw.ImageDraw, k: int) -> None:
        disc(draw, k, radius - 4, 255)
        disc(draw, k, radius - 4 - _RING_WIDTH, 0)

    _paint(canvas, box, (*colour, _HALO_ALPHA), halo, blur=_HALO_BLUR)
    _paint(canvas, box, (*colour, 255), ring)
    canvas.alpha_composite(_face_disc(face, size), (round(x - size / 2), round(y - size / 2)))


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


Point = tuple[float, float]


def _chevron(centre: tuple[float, float], direction: tuple[float, float]) -> list[Point]:
    """The three points of an open chevron (arm, tip, arm) around `centre`, its tip
    leading along the unit vector `direction`."""
    ux, uy = direction
    tip = (centre[0] + ux * _TRAIL_DEPTH / 2, centre[1] + uy * _TRAIL_DEPTH / 2)
    back = (centre[0] - ux * _TRAIL_DEPTH / 2, centre[1] - uy * _TRAIL_DEPTH / 2)
    reach = _TRAIL_HEIGHT / 2
    return [
        (back[0] - uy * reach, back[1] + ux * reach),
        tip,
        (back[0] + uy * reach, back[1] - ux * reach),
    ]


def _spirit_trail(
    canvas: Image.Image,
    start: tuple[int, int],
    end: tuple[int, int],
    colour: tuple[int, int, int],
) -> None:
    """A trail of chevrons from `start` to `end`, all pointing at `end`, a soft glow of
    the same chevrons under them."""
    length = math.dist(start, end)
    direction = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
    count = max(2, round(length / _TRAIL_SPACING) + 1)
    pad = _TRAIL_HEIGHT + 3 * 9
    x0, y0 = min(start[0], end[0]) - pad, min(start[1], end[1]) - pad
    box = (x0, y0, max(start[0], end[0]) + pad, max(start[1], end[1]) + pad)

    def chevrons(draw: ImageDraw.ImageDraw, k: int) -> None:
        half = _TRAIL_STROKE * k / 2
        for i in range(count):
            t = i / (count - 1)
            centre = (
                start[0] + (end[0] - start[0]) * t - x0,
                start[1] + (end[1] - start[1]) * t - y0,
            )
            points = [(x * k, y * k) for x, y in _chevron(centre, direction)]
            draw.line(points, fill=255, width=_TRAIL_STROKE * k, joint="curve")
            for x, y in (points[0], points[2]):  # round caps
                draw.ellipse((x - half, y - half, x + half, y + half), fill=255)

    _paint(canvas, box, (*colour, 255), chevrons, blur=9)
    _paint(canvas, box, (*colour, 255), chevrons)


def _torn_band(start: Point, end: Point) -> list[Point]:
    """A band `_CUT_WIDTH` wide along start -> end (run on past both ends), its two
    edges zigzagging by `_CUT_JAG` so it reads as torn, not sliced."""
    length = math.dist(start, end)
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    start = (start[0] - ux * 20, start[1] - uy * 20)
    steps = 12
    edges: list[list[Point]] = [[], []]
    for i in range(steps + 1):
        t = (length + 40) * i / steps
        for edge, side in zip(edges, (1, -1), strict=True):
            reach = _CUT_WIDTH / 2 + _CUT_JAG * (1 if (i + (side > 0)) % 2 else -1)
            edge.append(
                (start[0] + ux * t - uy * side * reach, start[1] + uy * t + ux * side * reach)
            )
    return edges[0] + edges[1][::-1]


def _cross_cut(size: tuple[int, int]) -> Image.Image:
    """An L mask of `size`: 255 where the art stays, 0 in the two torn diagonal bands."""
    width, height = size
    bands = [
        _torn_band((0, 0), (width, height)),
        _torn_band((width, 0), (0, height)),
    ]

    def cut(draw: ImageDraw.ImageDraw, k: int) -> None:
        draw.rectangle((0, 0, width * k, height * k), fill=255)
        for band in bands:
            draw.polygon([(x * k, y * k) for x, y in band], fill=0)

    return smooth_mask(size, cut)


def _quarters(size: tuple[int, int]) -> list[tuple[Image.Image, tuple[int, int]]]:
    """The top, right, bottom and left triangles of a `size` picture, as masks, each
    with the direction it moves when the cross opens."""
    width, height = size
    middle = (width / 2, height / 2)
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    pushes = [(0, -1), (1, 0), (0, 1), (-1, 0)]
    result = []
    for i, push in enumerate(pushes):
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).polygon([corners[i], corners[(i + 1) % 4], middle], fill=255)
        result.append((mask, push))
    return result


def _sparkle(canvas: Image.Image, centre: Point, radius: int) -> None:
    """A small golden four-point star."""
    x, y = round(centre[0]), round(centre[1])
    reach = radius + 2
    box = (x - reach, y - reach, x + reach, y + reach)

    def star(draw: ImageDraw.ImageDraw, k: int) -> None:
        mid, r, inner = reach * k, radius * k, radius * k * 0.28
        points = []
        for i in range(8):
            angle = math.pi / 4 * i - math.pi / 2
            d = r if i % 2 == 0 else inner
            points.append((mid + d * math.cos(angle), mid + d * math.sin(angle)))
        draw.polygon(points, fill=255)

    _paint(canvas, box, (*_SPARKLE, 255), star)


def _spent_art(canvas: Image.Image, art: Image.Image, centre: tuple[int, int]) -> None:
    """The attacking item drained and faded, torn along an X into four pieces that
    have moved apart, so the cross shows the background through."""
    picture = _art(art, _ATTACK_ART_BOX)
    drained = ImageEnhance.Color(picture.convert("RGB")).enhance(_FADED_COLOUR).convert("RGBA")
    drained.putalpha(picture.getchannel("A").point(lambda a: round(a * _FADED_ALPHA)))
    cut = ImageChops.multiply(drained.getchannel("A"), _cross_cut(drained.size))
    origin = (centre[0] - drained.width // 2, centre[1] - drained.height // 2)
    for mask, (dx, dy) in _quarters(drained.size):
        piece = drained.copy()
        piece.putalpha(ImageChops.multiply(cut, mask))
        canvas.alpha_composite(piece, (origin[0] + dx * _CUT_PUSH, origin[1] + dy * _CUT_PUSH))
    for (dx, dy), radius in _SPARKLES:
        _sparkle(canvas, (centre[0] + dx, centre[1] + dy), radius)


@dataclass(frozen=True)
class Duel:
    actor: Face  # who cast
    target: Face  # who it was cast at


def blocked_duel_card(
    mood: Mood, duel: Duel, attack: Image.Image, defence: Image.Image, title: str
) -> bytes:
    """A hex blocked: the target in the middle beside their `defence` art, while the
    `attack` art lies faded and torn into a cross next to the small caster's name."""
    canvas = _background(mood).copy()
    _title(canvas, title)
    _spent_art(canvas, attack, (230, 300))
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
