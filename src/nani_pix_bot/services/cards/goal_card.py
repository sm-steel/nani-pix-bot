"""The 100 Candles counter card (Halloween spec): the run's layered art
(background, the rising Gashadokuro, a skull pile that grows, the candles) and
the "snuffed / target" counter; the boss post is the run's boss picture alone.
Pure Pillow, no Telegram; all words are in the post's caption."""

from dataclasses import dataclass

from PIL import Image, ImageDraw

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
CANDLE_SLOTS: tuple[tuple[int, int], ...] = tuple((150 + 100 * i, 560) for i in range(10))
CANDLE_SCALE = 0.6
# Nothing of the Gashadokuro is drawn below this line: its sunk body stays hidden.
HORIZON_Y = 470
RISE_SCALE = (0.45, 1.0)  # the Gashadokuro's scale at no candles out / all out
RISE_SINK = 0.5  # the share of its height still below the horizon at the start
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


def _candles(image: Image.Image, run_id: str, snuffed: int, target: int) -> None:
    lit, out = (_load(run_id, name) for name in ("candle_lit.png", "candle_out.png"))
    size = (round(lit.width * CANDLE_SCALE), round(lit.height * CANDLE_SCALE))
    sprites = [art.resize(size, Image.Resampling.LANCZOS) for art in (lit, out)]
    burning = lit_candles(snuffed, target)
    for number, (x, y) in enumerate(CANDLE_SLOTS):
        image.alpha_composite(sprites[number >= burning], (x - size[0] // 2, y - size[1]))


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
