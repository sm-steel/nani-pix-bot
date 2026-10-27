"""The Halloween "lantern_string" stage frame: a dark cord sagging in two swags
across the top edge, with paper chochin lanterns hanging from it, each lit by a
faint warm glow. Deterministic. Cheap: the cord is drawn on a top strip, the glow
is one small cached gradient screened per lantern, and nothing is supersampled
full-canvas."""

import functools
import math
import random

from loguru import logger
from PIL import Image, ImageChops, ImageDraw

from nani_pix_bot.services import mascot, sprites
from nani_pix_bot.services.mascot import Pose

Color = tuple[int, int, int]

SPRITE = "chochin.png"
SEED = 37
CORD: Color = (40, 30, 30)
CORD_WIDTH = 0.006  # of the short side
SAG = 0.03  # of the height, per swag
SWAGS = 2
LANTERN_SPACING = 0.42  # of the height, between lanterns along the top
LANTERN_RATIO = 0.08  # of the short side
TILT = 4.0  # degrees either way
GLOW: Color = (255, 170, 80)
GLOW_RADIUS = 0.9  # of a lantern's height
_GLOW_STRENGTH = 0.3  # of full screen at the glow's heart: it stays subtle
_BODY = 0.6  # of a lantern's height, down to where its light sits
_MIN_LANTERN = 6  # px: smaller is just noise
_CORD_SS = 3
_CATENARY = 2.0  # how deep the swag hangs: larger sags harder in the middle

_missing_logged = False


def _swag(u: float) -> float:
    """0 at either end of a swag (u = -1 or 1), 1 at its lowest point (u = 0)."""
    return 1 - (math.cosh(_CATENARY * u) - 1) / (math.cosh(_CATENARY) - 1)


def _cord_y(x: float, w: int, sag: float, half: float) -> float:
    """The centre line of the cord at `x`: it hangs from the top edge at each swag's ends."""
    span = w / SWAGS
    u = ((x % span) / span if x < w else 1.0) * 2 - 1
    return half + sag * _swag(u)


def _draw_cord(out: Image.Image, sag: float, width: float) -> None:
    w = out.width
    strip_h = math.ceil(sag + width * 2)
    ss = _CORD_SS
    strip = Image.new("L", (w * ss, strip_h * ss), 0)
    pen = ImageDraw.Draw(strip)
    half = width / 2
    points = [(x * ss, _cord_y(x, w, sag, half) * ss) for x in (*range(w), w - 1)]
    pen.line(points, fill=255, width=max(1, round(width * ss)), joint="curve")
    mask = strip.resize((w, strip_h), Image.Resampling.BOX)
    out.paste(Image.new("RGB", mask.size, CORD), (0, 0), mask)


@functools.lru_cache(maxsize=4)
def _glow_mask(diameter: int) -> Image.Image:
    """A soft round falloff, brightest in the middle, 0..`_GLOW_STRENGTH` of 255."""
    base = Image.radial_gradient("L")  # 0 in the middle, 255 at the rim
    fall = base.point(lambda v: round(255 * _GLOW_STRENGTH * max(0.0, 1 - v / 255) ** 2))
    return fall.resize((diameter, diameter), Image.Resampling.BICUBIC)


def _lit(out: Image.Image, centre: tuple[float, float], radius: float) -> None:
    """Screen the glow round `centre` into `out`, clipped to the canvas."""
    d = max(2, round(radius * 2))
    left, top = round(centre[0] - d / 2), round(centre[1] - d / 2)
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(out.width, left + d), min(out.height, top + d)
    if x1 <= x0 or y1 <= y0:
        return
    mask = _glow_mask(d).crop((x0 - left, y0 - top, x1 - left, y1 - top))
    region = out.crop((x0, y0, x1, y1))
    lit = ImageChops.screen(region, Image.new("RGB", region.size, GLOW))
    out.paste(Image.composite(lit, region, mask), (x0, y0))


@functools.lru_cache(maxsize=24)
def _hung(height: int, tilt: float) -> Image.Image:
    """The lantern `height` tall, swung `tilt` degrees about the middle of its top
    edge, which sits at the top middle of the (wider) result."""
    art = sprites.sprite_height(SPRITE, height)
    side = max(art.width, height)
    canvas = Image.new("RGBA", (side, height), (0, 0, 0, 0))
    canvas.alpha_composite(art, ((side - art.width) // 2, 0))
    return canvas.rotate(tilt, resample=Image.Resampling.BICUBIC, center=(side / 2, 0))


def _limit(size: tuple[int, int], zone: float) -> int:
    """How far right a lantern may reach: Nani's left edge when she stands that high."""
    x, y, width, _ = mascot.box(size, Pose.NOPE)
    return x if width and y < zone else size[0]


def lantern_string(image: Image.Image) -> Image.Image:
    """The "lantern_string" frame: lit chochin lanterns on a sagging cord."""
    base = image.convert("RGB")
    w, h = base.size
    short = min(w, h)
    lantern_h = round(short * LANTERN_RATIO)
    if lantern_h < _MIN_LANTERN:
        return base
    out = base.copy()
    sag, width = h * SAG, max(1.0, short * CORD_WIDTH)
    half = width / 2
    rng = random.Random(SEED)  # noqa: S311 - seeded for a reproducible look
    count = max(3, round(w / (LANTERN_SPACING * h)))
    reach = _limit(base.size, sag + lantern_h * 1.2)
    pitch = reach / count
    spots = []
    for i in range(count):
        x = (i + 0.5) * pitch
        spots.append((x, _cord_y(x, w, sag, half), round(rng.uniform(-TILT, TILT), 1)))
    for x, y, _tilt in spots:
        _lit(out, (x, y + lantern_h * _BODY), lantern_h * GLOW_RADIUS)
    _draw_cord(out, sag, width)
    try:
        lanterns = [(x, y, _hung(lantern_h, tilt)) for x, y, tilt in spots]
    except OSError:  # a missing file: the cord and glow stay, a stage post never breaks
        global _missing_logged
        if not _missing_logged:
            _missing_logged = True
            logger.error("lantern art {name} is missing — posting without lanterns", name=SPRITE)
        return out
    canvas = out.convert("RGBA")
    for x, y, art in lanterns:
        canvas.alpha_composite(art, (round(x - art.width / 2), round(y)))
    return canvas.convert("RGB")
