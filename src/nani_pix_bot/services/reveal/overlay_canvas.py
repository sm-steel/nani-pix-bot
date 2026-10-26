"""The clip canvas shared by the reveal overlays (petals, Halloween): the overlay
area, the fade in/out, the soft edges and the supersampled frame loop. Runs in
the reveal worker: module-level functions, no logging."""

from collections.abc import Callable

from PIL import Image, ImageChops, ImageDraw

SS = 2  # supersampling of each frame
FADE_IN = 6
FADE_OUT = 14
EDGE_FADE = 0.7  # overlays dissolve over this many badge heights before the top and sides
EDGE_FADE_BOTTOM = 0.3


def clip_size(height: int) -> tuple[int, int]:
    """The overlay area for a badge `height`, the same as the confetti's."""
    return round(height * 7.5), height + round(height * 2.6)


def alpha(f: int, frames: int) -> int:
    fade = min(1.0, (f + 1) / FADE_IN, (frames - f) / FADE_OUT)
    return round(255 * max(0.0, fade))


def _smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def edge_fade(size: tuple[int, int], height: int) -> Image.Image:
    """An L mask over the overlay area: 255 inside, easing to 0 at its edges, so
    a shape leaving the area dissolves instead of being cut off on a straight line.
    The bottom fades over a shorter distance: the badge sits there."""
    w, h = size
    side, bottom = EDGE_FADE * height, EDGE_FADE_BOTTOM * height
    across = Image.new("L", (w, 1))
    for x in range(w):
        across.putpixel((x, 0), round(255 * _smooth(min(x, w - 1 - x) / side)))
    down = Image.new("L", (1, h))
    for y in range(h):
        down.putpixel((0, y), round(255 * min(_smooth(y / side), _smooth((h - 1 - y) / bottom))))
    return ImageChops.multiply(across.resize(size), down.resize(size))


def render(
    size: tuple[int, int],
    height: int,
    frames: int,
    draw_frame: Callable[[ImageDraw.ImageDraw, int, int], None],
) -> bytes:
    """Raw RGBA frames, one after another; `draw_frame(draw, f, alpha)` paints
    frame `f` onto a SS-times larger canvas, which then fades out at its edges."""
    w, h = size
    fade = edge_fade(size, height)
    raw = bytearray()
    for f in range(frames):
        big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        draw_frame(ImageDraw.Draw(big), f, alpha(f, frames))
        frame = big.resize(size, Image.Resampling.BOX)
        frame.putalpha(ImageChops.multiply(frame.getchannel("A"), fade))
        raw += frame.tobytes()
    return bytes(raw)
