"""The clue token's picture (seasons spec §4): a golden ticket with a magnifying glass and a
star on its stub, no text. Drawn here (no third-party art), once per process."""

import math
from functools import cache
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 512
SUPERSAMPLE = 4  # drawn at 4x and shrunk, for antialiasing
CANVAS = SIZE * SUPERSAMPLE
INK = (110, 70, 15)
EDGE = (150, 100, 25)


def _gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    width, height = size
    column = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / (height - 1)
        column.putpixel(
            (0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
        )
    return column.resize((width, height))


def _ticket_mask(box, radius: int, notch: int) -> Image.Image:
    x0, y0, x1, y1 = box
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(box, radius=radius, fill=255)
    cy = (y0 + y1) // 2
    draw.ellipse((x0 - notch, cy - notch, x0 + notch, cy + notch), fill=0)
    draw.ellipse((x1 - notch, cy - notch, x1 + notch, cy + notch), fill=0)
    return mask


def _paste_shadow(image: Image.Image, mask: Image.Image) -> None:
    shadow = mask.filter(ImageFilter.GaussianBlur(CANVAS * 0.025))
    shadow = ImageChops.offset(shadow, 0, int(CANVAS * 0.02))
    image.paste((10, 8, 18), (0, 0), shadow.point(lambda v: v * 0.7))


def _paste_gold(image: Image.Image, mask: Image.Image) -> None:
    """The metallic body: a diagonal gradient with a soft highlight band."""
    gold = _gradient((CANVAS, CANVAS), (255, 222, 140), (196, 140, 40)).rotate(
        -25, resample=Image.Resampling.BICUBIC
    )
    band = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(band).polygon(
        [(CANVAS * 0.30, 0), (CANVAS * 0.42, 0), (CANVAS * 0.22, CANVAS), (CANVAS * 0.10, CANVAS)],
        fill=90,
    )
    band = band.filter(ImageFilter.GaussianBlur(CANVAS * 0.03))
    gold = Image.composite(Image.new("RGB", (CANVAS, CANVAS), (255, 246, 214)), gold, band)
    image.paste(gold, (0, 0), mask)


def _paste_inner_border(image: Image.Image, box) -> None:
    x0, y0, x1, y1 = box
    inset = int(CANVAS * 0.025)
    inner = _ticket_mask(
        (x0 + inset, y0 + inset, x1 - inset, y1 - inset), int(CANVAS * 0.035), int(CANVAS * 0.07)
    )
    edge = ImageChops.subtract(inner, inner.filter(ImageFilter.MinFilter(int(CANVAS * 0.006) | 1)))
    image.paste(EDGE, (0, 0), edge)


def _draw_perforation(draw: ImageDraw.ImageDraw, box, x: int) -> None:
    _, y0, _, y1 = box
    radius = int(CANVAS * 0.008)
    y = y0 + int(CANVAS * 0.05)
    while y < y1 - int(CANVAS * 0.04):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=EDGE)
        y += int(CANVAS * 0.03)


def _draw_magnifier(image: Image.Image, draw: ImageDraw.ImageDraw, box, perforation: int) -> None:
    x0, y0, _, y1 = box
    cx, cy = x0 + int((perforation - x0) * 0.47), (y0 + y1) // 2 - int(CANVAS * 0.02)
    radius = int(CANVAS * 0.085)
    width = int(CANVAS * 0.022)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=INK, width=width)
    lens = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(lens).ellipse(
        (cx - radius + width, cy - radius + width, cx + radius - width, cy + radius - width),
        fill=70,
    )
    image.paste((255, 255, 255), (0, 0), lens)
    hx, hy = cx + int(radius * 0.70), cy + int(radius * 0.70)
    draw.line(
        (hx, hy, hx + int(CANVAS * 0.075), hy + int(CANVAS * 0.075)),
        fill=INK,
        width=int(width * 1.6),
    )


def _draw_star(draw: ImageDraw.ImageDraw, box, perforation: int) -> None:
    _, y0, x1, y1 = box
    sx, sy = perforation + (x1 - perforation) // 2 - int(CANVAS * 0.025), (y0 + y1) // 2
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = CANVAS * (0.05 if i % 2 == 0 else 0.021)
        points.append((sx + radius * math.cos(angle), sy + radius * math.sin(angle)))
    draw.polygon(points, fill=INK)


@cache
def icon() -> bytes:
    """The ticket as a 512x512 PNG; the same bytes every call."""
    image = _gradient((CANVAS, CANVAS), (46, 38, 74), (24, 20, 40))
    box = (int(CANVAS * 0.12), int(CANVAS * 0.27), int(CANVAS * 0.88), int(CANVAS * 0.73))
    mask = _ticket_mask(box, int(CANVAS * 0.05), int(CANVAS * 0.055))
    _paste_shadow(image, mask)
    _paste_gold(image, mask)
    _paste_inner_border(image, box)
    draw = ImageDraw.Draw(image)
    perforation = box[0] + int((box[2] - box[0]) * 0.66)
    _draw_perforation(draw, box, perforation)
    _draw_magnifier(image, draw, box, perforation)
    _draw_star(draw, box, perforation)
    out = BytesIO()
    image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).save(out, format="PNG")
    return out.getvalue()
