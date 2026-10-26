"""The 100 Candles counter card (Halloween spec): the run's layered art
(background, the rising Gashadokuro, a skull pile that grows, the candles) and
the "snuffed / target" counter; the boss post is the run's boss picture alone.
Pure Pillow, no Telegram; all words are in the post's caption."""

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from nani_pix_bot.services.cards import backgrounds
from nani_pix_bot.services.cards.render import _cover, _png, font

GOAL_ART = "goal"  # assets/seasons/<run_id>/goal/
GOAL_LAYERS = (
    "background.png",
    "skulls_0.png",
    "skulls_1.png",
    "skulls_2.png",
    "skulls_3.png",
    "skulls_4.png",
    "gashadokuro.png",
    "candle_lit.png",
    "candle_out.png",
)
BOSS_ART = "boss.png"
CARD_SIZE = (1200, 630)
PILES = 5
# Base-centre points of the ten candles on the 1200x630 canvas.
# Five flank the altar on each side; the lit ones are the first slots, left to right.
CANDLE_SLOTS: tuple[tuple[int, int], ...] = (
    (50, 600),
    (100, 585),
    (150, 603),
    (200, 588),
    (245, 606),
    (955, 606),
    (1000, 588),
    (1050, 603),
    (1100, 585),
    (1150, 600),
)
CANDLE_SCALE = 0.7
# Nothing of the Gashadokuro is drawn below this line: its sunk body stays hidden.
HORIZON_Y = 470
RISE_SCALE = (0.45, 0.56)  # the Gashadokuro's scale at no candles out / all out
RISE_SINK = 0.5  # the share of its height still below the horizon at the start
# Candle lighting: bodies take the scene's colour, lit ones glow faintly blue.
_FLAME = 0.30  # top share of a lit candle that is flame, kept unshaded
_SHADE = 0.85  # how far a body takes the scene's colour
_GLOW = (150, 200, 255)
_GLOW_RADIUS = 26
_GLOW_STRENGTH = 0.3
_POOL_BOX = (-40, -8, 40, 10)  # the floor's light pool, relative to the candle's base
_POOL_STRENGTH = 0.08
_POOL_BLUR = 8
_FLAME_AT = 0.12  # the glow's centre, as a share of the candle's height below its top
_SHADOW_COLOUR = (10, 5, 20, 150)
_SHADOW_BLUR = 3
_COUNTER_XY = (36, 28)
_COUNTER_SIZE = 56
_COUNTER_FILL = (255, 244, 220)
_COUNTER_STROKE = ((20, 12, 24), 5)


@dataclass(frozen=True)
class GoalCard:
    run_id: str
    snuffed: int
    target: int
    boss: bool = False


def _progress(snuffed: int, target: int) -> float:
    return min(1.0, max(0, snuffed) / target)


def pile_index(snuffed: int, target: int) -> int:
    return min(PILES - 1, PILES * min(max(0, snuffed), target) // target)


def lit_candles(snuffed: int, target: int) -> int:
    """Candles still burning: the slots' share of what is left to snuff, rounded up."""
    left = max(0, target - snuffed)
    return -(-len(CANDLE_SLOTS) * left // target)


def rise(snuffed: int, target: int) -> tuple[float, float]:
    """(scale, sink) of the Gashadokuro: it grows and climbs out as candles go out."""
    progress = _progress(snuffed, target)
    low, high = RISE_SCALE
    return low + (high - low) * progress, RISE_SINK * (1 - progress)


def _load(run_id: str, name: str) -> Image.Image:
    """One art file of the run's goal folder; FileNotFoundError when it's missing."""
    if name not in GOAL_LAYERS:
        raise ValueError(f"{name} is not a goal layer")
    path = backgrounds.SEASON_ART_DIR / run_id / GOAL_ART / name
    with Image.open(path) as image:
        return image.convert("RGBA")


def _gashadokuro(run_id: str, snuffed: int, target: int) -> Image.Image:
    """The skeleton on a canvas-sized layer: bottom on the horizon, sunk, clipped there."""
    scale, sink = rise(snuffed, target)
    art = _load(run_id, "gashadokuro.png")
    size = (round(art.width * scale), round(art.height * scale))
    sprite = art.resize(size, Image.Resampling.LANCZOS)
    top = HORIZON_Y - sprite.height + round(sink * sprite.height)
    layer = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    layer.paste(sprite, ((CARD_SIZE[0] - sprite.width) // 2, top))
    layer.paste((0, 0, 0, 0), (0, HORIZON_Y, CARD_SIZE[0], CARD_SIZE[1]))
    return layer


def _tint(image: Image.Image, x: int, y: int, size: tuple[int, int]) -> tuple[int, ...]:
    """The scene's colour around a candle slot: the mean of the background there, lifted."""
    w, h = size[0] * 2, size[1]
    box = (max(0, x - w), max(0, y - h), min(image.width, x + w), min(image.height, y))
    mean = ImageStat.Stat(image.crop(box).convert("RGB")).mean
    return tuple(min(255, int(20 + c * 1.35)) for c in mean)


def _shade(sprite: Image.Image, tint: tuple[int, ...], lit: bool) -> Image.Image:
    """The candle's body multiplied toward the scene colour, darker toward its base;
    a lit candle keeps its flame unshaded."""
    rgb = sprite.convert("RGB")
    tinted = ImageChops.multiply(rgb, Image.new("RGB", rgb.size, tint))
    mask = Image.new("L", rgb.size, int(255 * _SHADE))
    if lit:
        top = int(rgb.height * _FLAME)
        ramp = Image.linear_gradient("L").resize(rgb.size).crop((0, 0, rgb.width, rgb.height - top))
        body = ramp.point(lambda v: int(255 * _SHADE * min(1.0, 0.35 + v / 255 * 2.5)))
        mask = Image.new("L", rgb.size, 0)
        mask.paste(body, (0, top))
    out = Image.composite(tinted, rgb, mask)
    fade = Image.linear_gradient("L").resize(rgb.size).point(lambda v: max(0, int((v - 120) * 1.4)))
    dark = ImageChops.multiply(out, Image.new("RGB", rgb.size, tuple(c // 2 for c in tint)))
    out = Image.composite(dark, out, fade)
    out.putalpha(sprite.getchannel("A"))
    return out


def _ellipse_layer(
    size: tuple[int, int], box: tuple[int, int, int, int], strength: float, blur: float
) -> Image.Image:
    """A blurred blue-white ellipse on black, to be added to the card's light layer."""
    layer = Image.new("RGB", size, (0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=tuple(int(c * strength) for c in _GLOW))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _glow(size: tuple[int, int], centre: tuple[int, int]) -> Image.Image:
    cx, cy = centre
    r = _GLOW_RADIUS
    return _ellipse_layer(size, (cx - r, cy - r, cx + r, cy + r), _GLOW_STRENGTH, r / 2)


def _pool(size: tuple[int, int], base: tuple[int, int]) -> Image.Image:
    x, y = base
    left, top, right, bottom = _POOL_BOX
    box = (x + left, y + top, x + right, y + bottom)
    return _ellipse_layer(size, box, _POOL_STRENGTH, _POOL_BLUR)


def _shadow(image: Image.Image, base: tuple[int, int], half_width: int) -> None:
    x, y = base
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        (x - half_width, y - 4, x + half_width, y + 4), fill=_SHADOW_COLOUR
    )
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR)))


def _candles(image: Image.Image, run_id: str, snuffed: int, target: int) -> None:
    lit, out = (_load(run_id, name) for name in ("candle_lit.png", "candle_out.png"))
    size = (round(lit.width * CANDLE_SCALE), round(lit.height * CANDLE_SCALE))
    sprites = [art.resize(size, Image.Resampling.LANCZOS) for art in (lit, out)]
    burning = lit_candles(snuffed, target)
    light = Image.new("RGB", image.size, (0, 0, 0))
    for number, (x, y) in enumerate(CANDLE_SLOTS):
        on = number < burning
        _shadow(image, (x, y), size[0] // 2 + 4)
        sprite = _shade(sprites[not on], _tint(image, x, y, size), on)
        image.alpha_composite(sprite, (x - size[0] // 2, y - size[1]))
        if on:
            flame = (x, y - size[1] + int(size[1] * _FLAME_AT))
            light = ImageChops.add(light, _glow(image.size, flame))
            light = ImageChops.add(light, _pool(image.size, (x, y)))
    image.paste(ImageChops.screen(image.convert("RGB"), light))


def _counter(image: Image.Image, snuffed: int, target: int) -> None:
    stroke_fill, stroke_width = _COUNTER_STROKE
    ImageDraw.Draw(image).text(
        _COUNTER_XY,
        f"{snuffed} / {target}",
        font=font(_COUNTER_SIZE, bold=True),
        fill=_COUNTER_FILL,
        stroke_fill=stroke_fill,
        stroke_width=stroke_width,
    )


def _boss(run_id: str) -> Image.Image:
    path = backgrounds.SEASON_ART_DIR / run_id / GOAL_ART / BOSS_ART
    picture = _cover(path.read_bytes(), CARD_SIZE)
    if picture is None:
        raise ValueError(f"{path} is not an image")
    return picture


def render_goal_card(card: GoalCard) -> bytes:
    """The counter card as a 1200x630 PNG, or the boss picture alone. A missing
    art file raises FileNotFoundError (the poster falls back to text)."""
    if card.boss:
        return _png(_boss(card.run_id))
    image = _load(card.run_id, "background.png").copy()
    if image.size != CARD_SIZE:
        image = image.resize(CARD_SIZE, Image.Resampling.LANCZOS)
    image.alpha_composite(_gashadokuro(card.run_id, card.snuffed, card.target))
    pile = pile_index(card.snuffed, card.target)
    image.alpha_composite(_load(card.run_id, f"skulls_{pile}.png"))
    _candles(image, card.run_id, card.snuffed, card.target)
    _counter(image, card.snuffed, card.target)
    return _png(image.convert("RGB"))
