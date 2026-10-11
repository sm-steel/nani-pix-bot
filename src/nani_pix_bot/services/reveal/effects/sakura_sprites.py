"""The sakura branch's pieces for the blossom branch reveal, each drawn once,
supersampled: the whole branch (tapering bark, a lighter top edge, a soft
shadow), a blossom and a leaf. blossom_branch.py decides when each shows."""

import math
from collections.abc import Sequence

from PIL import Image, ImageDraw, ImageFilter

from nani_pix_bot.services.shapes import petal_outline

Point = tuple[float, float]
SS = 2  # supersampling
BARK = (66, 44, 46)
BARK_LIGHT = (104, 74, 72)
PETAL = (255, 222, 232)
PETAL_EDGE = (236, 160, 186)
CENTRE = (214, 70, 110)
LEAF = (242, 150, 182)
LEAF_EDGE = (204, 92, 134)
LEAF_VEIN = (255, 208, 224)


def leaf_sprite(length: int, angle: float) -> Image.Image:
    """A sakura leaf, stalk at the centre of the sprite and pointing along `angle`: a pointed
    oval with a finely serrated edge, sakura pink with a rose rim and a pale midrib."""
    big = length * SS * 2
    image = Image.new("RGBA", (big * 2, big * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cx = cy = big
    reach, half = big * 0.95, big * 0.2
    teeth = 18
    edge = []
    for i in range(teeth + 1):
        u = i / teeth
        notch = 1.0 if i % 2 else 0.86  # the serration
        edge.append((cx + reach * u, half * math.sin(math.pi * u) ** 0.75 * notch))
    outline = [(x, cy - v) for x, v in edge] + [(x, cy + v) for x, v in reversed(edge)]
    draw.polygon(outline, fill=(*LEAF, 255), outline=(*LEAF_EDGE, 255), width=max(1, big // 50))
    vein = ((cx, cy), (cx + reach * 0.92, cy))
    draw.line(vein, fill=(*LEAF_VEIN, 255), width=max(1, big // 45))
    turned = image.rotate(angle, Image.Resampling.BICUBIC)
    side = length * 2
    return turned.resize((side, side), Image.Resampling.LANCZOS)


def blossom_sprite(diameter: int) -> Image.Image:
    """One open blossom, drawn big and shrunk: five notched petals and a deep pink centre."""
    big = diameter * SS * 2
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    centre = big / 2
    for k in range(5):
        angle = -math.pi / 2 + k * math.tau / 5
        petal = petal_outline((centre, centre), big * 0.47, big * 0.21, angle)
        draw.polygon(petal, fill=(*PETAL, 255), outline=(*PETAL_EDGE, 255), width=max(1, big // 60))
    core = big * 0.1
    draw.ellipse((centre - core, centre - core, centre + core, centre + core), fill=(*CENTRE, 255))
    return image.resize((diameter, diameter), Image.Resampling.LANCZOS)


def branch_layer(
    paths: Sequence[tuple[Sequence[Point], float]], size: tuple[int, int]
) -> Image.Image:
    """The whole branch, drawn once: tapering bark with a lighter top edge and a soft shadow."""
    w, h = size
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    for points, base_width in paths:
        n = len(points)
        for i in range(1, n):
            width = max(1.0, base_width * (1 - 0.75 * i / n)) * SS
            (x0, y0), (x1, y1) = points[i - 1], points[i]
            a, b = (x0 * SS, y0 * SS), (x1 * SS, y1 * SS)
            draw.line((a, b), fill=(*BARK, 255), width=round(width))
            r = width / 2
            draw.ellipse((b[0] - r, b[1] - r, b[0] + r, b[1] + r), fill=(*BARK, 255))
            light = (a[0], a[1] - r * 0.45), (b[0], b[1] - r * 0.45)
            draw.line(light, fill=(*BARK_LIGHT, 255), width=max(1, round(width / 4)))
    branch = big.resize(size, Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow.putalpha(branch.getchannel("A").point(lambda v: v * 2 // 5))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, h // 180)))
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    offset = max(1, h // 300)
    out.alpha_composite(shadow, (offset, offset))
    out.alpha_composite(branch)
    return out
