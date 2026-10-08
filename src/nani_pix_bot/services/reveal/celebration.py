"""The winner's celebration for the reveal video (issue #295): a gold badge (avatar, @handle,
crown, the achievement cards' gold ring and glow), its pop-in + shine frames and a confetti burst.

Pure functions; `encode.ending_and_join` overlays the results onto the clear screenshot. Everything
that doesn't depend on the winner is cached per size (per worker process), so a repeat winner or a
second game at the same resolution only pays for the face and the handle.
"""

import functools
import math
import random
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services.cards import render as cards
from nani_pix_bot.services.reveal import encode

CONFETTI_FRAMES = 40
CONFETTI_PARTICLES = 70
CONFETTI_SEED = 5
POP_N = 9  # frames of the pop-in
SHINE = (8, 22)  # first/last frame of the shine sweep
SS = 2  # supersampling of the pill and the handle text
GOLD = cards.RARITY_COLORS[Rarity.GOLD]
CONFETTI_COLORS = ((255, 214, 64), (255, 240, 170), (240, 150, 30), (255, 105, 160), (90, 220, 230),
                   (255, 255, 255))  # fmt: skip


def badge_height(size: tuple[int, int]) -> int:
    """The badge pill's height for a frame of `size`."""
    return round(max(48, size[1] // 11) * 1.5)


@functools.lru_cache(maxsize=4)
def _font(px: int) -> ImageFont.FreeTypeFont:
    return cards.font(px, bold=True)


@functools.lru_cache(maxsize=8)
def _gold_sweep(size: int) -> Image.Image:
    return cards.sweep(size, 0, size, cards._SWEEP[Rarity.GOLD]).convert("RGBA")


@functools.lru_cache(maxsize=8)
def _crown(px: int) -> Image.Image:
    s = px * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    base_top, base_bot = s * 0.62, s * 0.86
    edge = (120, 70, 10, 255)
    pts = [(s * 0.08, base_top), (s * 0.12, s * 0.30), (s * 0.32, s * 0.50), (s * 0.50, s * 0.16),
           (s * 0.68, s * 0.50), (s * 0.88, s * 0.30), (s * 0.92, base_top)]  # fmt: skip
    d.polygon([*pts, (s * 0.92, base_bot), (s * 0.08, base_bot)], fill=(255, 205, 50, 255),
              outline=edge, width=s // 28)  # fmt: skip
    d.rectangle((s * 0.08, base_top, s * 0.92, base_bot), outline=edge, width=s // 28)
    d.rectangle((s * 0.12, base_top + s * 0.03, s * 0.88, base_top + s * 0.09),
                fill=(255, 245, 190, 255))  # fmt: skip
    for x, y in ((0.12, 0.30), (0.50, 0.16), (0.88, 0.30)):
        r = s * 0.07
        d.ellipse((s * x - r, s * y - r, s * x + r, s * y + r), fill=(255, 90, 120, 255),
                  outline=edge, width=s // 40)  # fmt: skip
    small = im.resize((px, px), Image.Resampling.LANCZOS)
    return small.rotate(18, expand=True, resample=Image.Resampling.BICUBIC)


@dataclass(frozen=True)
class _Ring:
    """The gold avatar ring + glow from the achievement cards, drawn once per size with the
    face area left as a flat gold disc; the winner's face is pasted into it at win time."""

    image: Image.Image  # final-size RGBA
    face_box: tuple[int, int]  # top-left of the face disc inside image


@functools.lru_cache(maxsize=4)
def _ring(face_px: int) -> _Ring:
    side = face_px * cards._SS
    image = cards.badge_ring(Image.new("RGBA", (side, side), (0, 0, 0, 0)), GOLD, Rarity.GOLD)
    corner = (image.width - face_px) // 2
    return _Ring(image, (corner, corner))


def _face(avatar: bytes | None, handle: str, px: int) -> Image.Image:
    """The avatar disc at 4x then shrunk once, so its edge is as smooth as the cards' version."""
    big = cards.avatar_disc(avatar, handle, 1, px * 4).convert("RGBA")
    return big.resize((px, px), Image.Resampling.LANCZOS)


@functools.lru_cache(maxsize=64)
def _pill(w: int, h: int, border: int) -> Image.Image:
    """Gold-bordered dark pill at final size (cached per width: repeat winners are free)."""
    big_w, big_h, big_b = w * SS, h * SS, border * SS
    mask = Image.new("L", (big_w, big_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big_w - 1, big_h - 1), radius=big_h // 2, fill=255
    )
    side = max(big_w, big_h)
    q = -(-side // 64) * 64
    pill = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    pill.paste(_gold_sweep(q).crop((0, 0, big_w, big_h)), (0, 0), mask)
    ImageDraw.Draw(pill).rounded_rectangle(
        (big_b, big_b, big_w - 1 - big_b, big_h - 1 - big_b),
        radius=big_h // 2 - big_b,
        fill=(24, 16, 40, 225),
    )
    return pill.resize((w, h), Image.Resampling.LANCZOS)


def _handle_strip(text: str, h: int, tw: int, font_px: int) -> Image.Image:
    """The @handle at SS on a tight strip, shrunk once."""
    stroke = max(2, h * SS // 30)
    strip_h = round(font_px * 1.5)
    strip = Image.new("RGBA", (tw * SS + 4 * stroke, strip_h), (0, 0, 0, 0))
    ImageDraw.Draw(strip).text(
        (2 * stroke, strip_h // 2),
        text,
        font=_font(font_px),
        anchor="lm",
        fill=(255, 244, 205, 255),
        stroke_width=stroke,
        stroke_fill=(70, 40, 0, 255),
    )
    return strip.resize((strip.width // SS, strip.height // SS), Image.Resampling.LANCZOS)


def make_badge(avatar: bytes | None, label: str, height: int) -> Image.Image:
    """The winner badge (pill `height` px tall), built at final size from cached parts: per winner
    only the face is pasted into a pre-drawn gold ring and `label` is drawn. `label` is shown
    verbatim: the caller builds "@username", or the plain name when there is no username."""
    h = height
    face_px = round(h * 0.70)
    ring = _ring(face_px)
    ring_d = face_px + 2 * 14  # face + both rings
    font_px = round(h * 0.46 * SS)
    tw = math.ceil(_font(font_px).getlength(label) / SS)
    gap = round(h * 0.25)
    w = ring_d + gap + tw + round(h * 0.42)  # pill starts under the avatar's centre
    crown = _crown(round(h * 0.62))
    top_room = round(h * 0.62) // 2
    size = (w + ring.image.width // 2, h + top_room + ring.image.height // 2)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    ox, oy = ring.image.width // 2 - ring_d // 4, top_room + (ring.image.height - h) // 2
    canvas.alpha_composite(_pill(w, h, max(2, h // 11)), (ox, oy))
    acx, acy = ox + h // 2, oy + h // 2
    rx, ry = acx - ring.image.width // 2, acy - ring.image.height // 2
    canvas.alpha_composite(ring.image, (rx, ry))
    face_at = (rx + ring.face_box[0], ry + ring.face_box[1])
    canvas.alpha_composite(_face(avatar, label, face_px), face_at)
    crown_at = (max(0, acx - round(ring_d * 0.62)), max(0, acy - round(ring_d * 0.95)))
    canvas.alpha_composite(crown, crown_at)
    strip = _handle_strip(label, h, tw, font_px)
    stroke = max(2, h * SS // 30)
    tx = acx + ring_d // 2 + gap
    canvas.alpha_composite(strip, (tx - 2 * stroke // SS, oy + h // 2 - strip.height // 2))
    return canvas.crop(canvas.getbbox())


def _ease_out_back(t: float, s: float = 2.2) -> float:
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


_SMAX = max(_ease_out_back((f + 1) / POP_N) for f in range(POP_N))


@functools.lru_cache(maxsize=4)
def _shine_band(bh: int) -> tuple[Image.Image, int]:
    """The blurred diagonal glint, drawn + blurred once per badge height; slid across per frame.
    Returns the strip and its x offset relative to the sweep position cx."""
    r = math.ceil(bh * 0.05) * 3 + 2  # BoxBlur reach, so the strip never clips the blur
    x0 = -bh * 0.6 - r
    strip = Image.new("L", (math.ceil(bh * 1.05) + 2 * r, bh), 0)
    ImageDraw.Draw(strip).polygon(
        [(-x0, 0), (bh * 0.45 - x0, 0), (-bh * 0.15 - x0, bh), (-bh * 0.6 - x0, bh)], fill=150
    )
    return strip.filter(ImageFilter.BoxBlur(bh * 0.05)), math.floor(x0)


def _shine_frame(rest: Image.Image, badge: Image.Image, k: float) -> bytes:
    """The resting badge with the glint `k` (0..1) of the way across it."""
    bw, bh = badge.size
    ch = rest.height
    glint, gx = _shine_band(bh)
    band = Image.new("L", (bw, bh), 0)
    band.paste(glint, (round(-bh + k * (bw + 2 * bh)) + gx, 0))
    im = rest.copy()
    mask = ImageChops.multiply(band, badge.getchannel("A"))
    im.paste((255, 255, 240), (0, ch - bh, bw, ch), mask)
    return im.tobytes()


def badge_frames(badge: Image.Image) -> tuple[bytes, tuple[int, int]]:
    """Raw RGBA: pop-in with overshoot, one shine sweep, then the resting badge (last frame,
    which ffmpeg repeats). Fixed canvas big enough for the overshoot, badge anchored bottom-left."""
    bw, bh = badge.size
    cw, ch = math.ceil(bw * _SMAX), math.ceil(bh * _SMAX)
    rest = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    rest.paste(badge, (0, ch - bh))
    rest_bytes = rest.tobytes()
    out = []
    for f in range(SHINE[1] + 2):
        s = _ease_out_back(min(1.0, (f + 1) / POP_N))
        if abs(s - 1) > 1e-3:
            im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            scaled = (max(1, round(bw * s)), max(1, round(bh * s)))
            b = badge.resize(scaled, Image.Resampling.BILINEAR)
            im.paste(b, (0, ch - b.height))
            out.append(im.tobytes())
        elif SHINE[0] <= f <= SHINE[1]:
            out.append(_shine_frame(rest, badge, (f - SHINE[0]) / (SHINE[1] - SHINE[0])))
        else:
            out.append(rest_bytes)
    return b"".join(out), (cw, ch)


@dataclass(frozen=True)
class _Particle:
    x: float
    y: float
    vx: float
    vy: float
    rotation: float
    spin: float
    width: float
    length: float
    color: tuple[int, int, int]
    delay: int


def _particles(bh: int, origin_y: float) -> list[_Particle]:
    rng = random.Random(CONFETTI_SEED)  # noqa: S311 - decoration, not security
    parts: list[_Particle] = []
    for _ in range(CONFETTI_PARTICLES):
        a = math.radians(rng.uniform(-170, -10))
        v = rng.uniform(0.35, 1.0) * bh * 0.55
        parts.append(
            _Particle(
                x=rng.uniform(bh * 0.6, bh * 4.2),
                y=origin_y,
                vx=math.cos(a) * v * 1.3,
                vy=math.sin(a) * v,
                rotation=rng.uniform(0, 6.3),
                spin=rng.uniform(-0.5, 0.5),
                width=rng.uniform(0.05, 0.09) * bh,
                length=rng.uniform(0.09, 0.16) * bh,
                color=rng.choice(CONFETTI_COLORS),
                delay=rng.randint(2, 6),
            )
        )
    return parts


def _draw_particle(d: ImageDraw.ImageDraw, p: _Particle, f: int, bh: int, alpha: int) -> None:
    t = f - p.delay
    if t < 0:
        return
    x, y = p.x + p.vx * t, p.y + p.vy * t + 0.5 * bh * 0.045 * t * t
    ang = p.rotation + p.spin * t
    hw, hl = p.width / 2 * (abs(math.cos(ang * 1.7)) * 0.8 + 0.2), p.length / 2
    ca, sa = math.cos(ang), math.sin(ang)
    corners = ((-hw, -hl), (hw, -hl), (hw, hl), (-hw, hl))
    d.polygon(
        [(x + ca * dx - sa * dy, y + sa * dx + ca * dy) for dx, dy in corners],
        fill=(*p.color, alpha),
    )


def confetti_clip(height: int) -> tuple[bytes, tuple[int, int]]:
    """The confetti burst (depends only on the badge `height`) as a qtrle/argb .mov and its
    frame size."""
    size = (round(height * 7.5), height + round(height * 2.6))
    parts = _particles(height, size[1] - height * 0.5)
    raw = bytearray()
    for f in range(CONFETTI_FRAMES):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        fade = 1.0 if f < CONFETTI_FRAMES - 12 else max(0.0, (CONFETTI_FRAMES - f) / 12)
        for p in parts:
            _draw_particle(d, p, f, height, round(255 * fade))
        raw += im.tobytes()
    return encode.encode_rgba_clip(bytes(raw), size), size
