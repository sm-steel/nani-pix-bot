"""Blossom branch reveal, part 1 (Sakura Season, seasons spec §7): a sakura
branch is drawn across the pixelated image, twigs splitting off it; buds along
it pop open into blossoms and sakura leaves unfurl, and around each opening
blossom the clear image spreads outward in a soft round patch until the
patches have merged. Then the branch is erased the way it was drawn: from its
base along the same path, its blossoms and leaves shrinking away as the
eraser passes them.

Every point of a path has a time: when the drawing reaches it. The eraser
reaches it ERASE_DELAY later. Cheap per frame: the whole branch is drawn
once, supersampled, and each frame only shows the stretch between the eraser
and the pen (a mask along the path); each blossom and leaf is one sprite,
rendered once and resized as it opens and closes; the reveal mask is drawn at
quarter size and stretched. Seeded, so the same image always grows the same
branch."""

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import sakura_sprites
from nani_pix_bot.services.reveal.effects._common import ease_in_out, ease_out_back

GROW_S = 1.1  # the pen draws the main branch from end to end
ERASE_DELAY = GROW_S * 0.5  # the eraser starts when the main branch is half drawn
OPEN_S = 0.28  # a bud opening into a blossom (and shrinking away again)
UNFURL_S = 0.35  # a leaf growing to full size (and away again)
SPREAD_S = 0.9  # a blossom's patch of clear image reaching its full size
CLEAR_S = 0.3  # at the very end, whatever is still pixelated clears
STEP = 0.022  # of the width: one step of the branch's path
TWIGS = 3
BUD_EVERY = 0.07  # of the width, along the path
SPREAD = 0.55  # of the diagonal: how far a blossom's patch reaches
MASK_SCALE = 4  # the reveal mask is drawn this many times smaller
SEED = 31
CRF = 23

Point = tuple[float, float]


@dataclass(frozen=True)
class _Path:
    points: list[Point]  # in px
    start: float  # seconds: when the pen starts it
    speed: float  # px per second
    width: float  # px at its base

    def drawn(self, t: float) -> int:
        """How many points of the path the pen has drawn by `t`."""
        if t <= self.start:
            return 0
        reach = (t - self.start) * self.speed
        total = 0.0
        for i in range(1, len(self.points)):
            total += math.dist(self.points[i - 1], self.points[i])
            if total > reach:
                return i
        return len(self.points)

    def time_at(self, index: int) -> float:
        """When the pen reaches point `index`."""
        length = sum(math.dist(self.points[i - 1], self.points[i]) for i in range(1, index + 1))
        return self.start + length / self.speed

    @property
    def ends(self) -> float:
        return self.time_at(len(self.points) - 1)


@dataclass(frozen=True)
class _Blossom:
    x: float
    y: float
    at: float  # when the pen passes its spot
    opens: float  # when it starts opening (a moment after `at`)
    size: float  # px, diameter when fully open
    turn: float  # degrees


@dataclass(frozen=True)
class _Leaf:
    x: float  # where its stalk meets the branch
    y: float
    at: float  # when the pen passes its spot
    unfurls: float  # when it starts unfurling
    length: float  # px
    angle: float  # degrees, PIL's counter-clockwise


@dataclass(frozen=True)
class _Course:
    """Where a path is headed: it wanders, but keeps turning back toward `heading`
    and away from the top and bottom `margin` of the image."""

    heading: float
    height: float
    margin: float = 0.18  # of the height


def _walk(
    start: Point, course: _Course, steps: int, step: float, rng: random.Random
) -> list[Point]:
    points = [start]
    heading = course.heading
    low, high = course.height * course.margin, course.height * (1 - course.margin)
    for _ in range(steps):
        x, y = points[-1]
        heading += rng.uniform(-0.22, 0.22) + 0.18 * (course.heading - heading)
        if y < low:
            heading += 0.12  # turn down, away from the top
        elif y > high:
            heading -= 0.12  # turn up, away from the bottom
        points.append((x + math.cos(heading) * step, y + math.sin(heading) * step))
    return points


def _paths(size: tuple[int, int], rng: random.Random) -> list[_Path]:
    w, h = size
    step = STEP * w
    main_steps = math.ceil(1.15 / STEP)
    main = _walk((-0.04 * w, 0.72 * h), _Course(math.radians(-12), h), main_steps, step, rng)
    speed = main_steps * step / GROW_S
    trunk = _Path(main, 0.0, speed, 0.022 * h)
    paths = [trunk]
    for i in range(TWIGS):
        root = round(len(main) * (0.25 + 0.22 * i + rng.uniform(-0.05, 0.05)))
        x0, y0 = main[root - 1]
        x1, y1 = main[root]
        along = math.atan2(y1 - y0, x1 - x0)
        side = -1 if i % 2 == 0 else 1  # alternate up and down
        course = _Course(along + side * rng.uniform(0.6, 0.9), h, margin=0.06)
        twig = _walk(main[root], course, rng.randint(9, 14), step * 0.8, rng)
        paths.append(_Path(twig, trunk.time_at(root), speed * 0.8, trunk.width * 0.5))
    return paths


def _blossoms(paths: list[_Path], size: tuple[int, int], rng: random.Random) -> list[_Blossom]:
    w, h = size
    every = max(2, round(BUD_EVERY / STEP))
    out = []
    for path in paths:
        for index in range(every // 2, len(path.points), every):
            x, y = path.points[index]
            if not (-0.05 * w < x < 1.05 * w and -0.05 * h < y < 1.05 * h):
                continue
            at = path.time_at(index)
            out.append(
                _Blossom(
                    x=x + rng.uniform(-0.4, 0.4) * path.width,
                    y=y + rng.uniform(-0.4, 0.4) * path.width,
                    at=at,
                    opens=at + rng.uniform(0.02, 0.12),
                    size=h * rng.uniform(0.07, 0.1),
                    turn=rng.uniform(0, 72),
                )
            )
    return out


def _leaves(paths: list[_Path], size: tuple[int, int], rng: random.Random) -> list[_Leaf]:
    """Leaves between the buds, alternating sides of the path, angled outward."""
    _w, h = size
    every = max(2, round(BUD_EVERY / STEP))
    out = []
    for path in paths:
        side = 1
        for index in range(every, len(path.points) - 1, every):
            (x0, y0), (x1, y1) = path.points[index], path.points[index + 1]
            along = math.degrees(math.atan2(-(y1 - y0), x1 - x0))  # PIL turns counter-clockwise
            side = -side
            at = path.time_at(index)
            out.append(
                _Leaf(
                    x=x0,
                    y=y0,
                    at=at,
                    unfurls=at + rng.uniform(0.03, 0.1),
                    length=h * rng.uniform(0.09, 0.12),
                    angle=along + side * rng.uniform(55, 80),
                )
            )
    return out


def _bark_mask(paths: list[_Path], t: float, size: tuple[int, int]) -> Image.Image:
    """Covers the stretch of each path between the eraser and the pen (generously wide:
    the bark sits inside it)."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for path in paths:
        drawn, erased = path.drawn(t), path.drawn(t - ERASE_DELAY)
        stretch = path.points[max(0, erased - 1) : drawn]
        if len(stretch) >= 2 and drawn > erased:
            draw.line(stretch, fill=255, width=round(path.width * 2.5), joint="curve")
    return mask


def _reveal_mask(blossoms: list[_Blossom], t: float, size: tuple[int, int]) -> Image.Image:
    """255 where the clear image shows: soft round patches spreading from the open blossoms."""
    w, h = size
    small = (max(1, w // MASK_SCALE), max(1, h // MASK_SCALE))
    mask = Image.new("L", small, 0)
    draw = ImageDraw.Draw(mask)
    reach = SPREAD * math.hypot(w, h) / MASK_SCALE
    for b in blossoms:
        k = (t - b.opens) / SPREAD_S
        if k <= 0:
            continue
        r = reach * ease_in_out(min(1.0, k))
        x, y = b.x / MASK_SCALE, b.y / MASK_SCALE
        draw.ellipse((x - r, y - r, x + r, y + r), fill=255)
    soft = mask.filter(ImageFilter.GaussianBlur(max(1, small[1] // 30)))
    return soft.resize(size, Image.Resampling.BILINEAR)


def _scale(t: float, appears: float, at: float, grow_s: float, opening) -> float:
    """How big a blossom or leaf is at `t`: growing in from `appears`, shrinking away
    once the eraser reaches its spot (`at` + ERASE_DELAY). 0 = not shown."""
    k_in = (t - appears) / grow_s
    if k_in <= 0:
        return 0.0
    k_out = (t - at - ERASE_DELAY) / grow_s
    return opening(min(1.0, k_in)) * (1 - ease_in_out(max(0.0, min(1.0, k_out))))


def _total_s(paths: list[_Path]) -> float:
    """Until the eraser has finished the last path and its last sprite has shrunk away."""
    return max(p.ends for p in paths) + ERASE_DELAY + max(OPEN_S, UNFURL_S)


def frames(original: Image.Image, stages: list[Image.Image]) -> Iterator[bytes]:
    clear = original.convert("RGB")
    front = stages[0].convert("RGB")
    size = clear.size
    rng = random.Random(SEED)  # noqa: S311 - visual randomness, seeded for a reproducible look
    paths = _paths(size, rng)
    blossoms = _blossoms(paths, size, rng)
    leaves = _leaves(paths, size, rng)
    branch = sakura_sprites.branch_layer([(p.points, p.width) for p in paths], size)
    sprites = {
        b: sakura_sprites.blossom_sprite(round(b.size)).rotate(b.turn, Image.Resampling.BICUBIC)
        for b in blossoms
    }
    leaf_sprites = {
        leaf: sakura_sprites.leaf_sprite(round(leaf.length), leaf.angle) for leaf in leaves
    }
    total = _total_s(paths)
    for i in range(math.ceil(total * encode.FPS) + 1):
        t = min(total, i / encode.FPS)
        reveal = _reveal_mask(blossoms, t, size)
        clearing = ease_in_out(max(0.0, (t - (total - CLEAR_S)) / CLEAR_S))
        if clearing > 0:  # whatever is still pixelated clears at the very end
            reveal = ImageChops.lighter(reveal, Image.new("L", size, round(255 * clearing)))
        frame = Image.composite(clear, front, reveal).convert("RGBA")
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        layer.paste(branch, (0, 0), _bark_mask(paths, t, size))
        for leaf in leaves:  # under the blossoms
            scale = _scale(t, leaf.unfurls, leaf.at, UNFURL_S, ease_in_out)
            if scale > 0:
                _grow_onto(layer, leaf_sprites[leaf], (leaf.x, leaf.y), scale)
        for b in blossoms:
            scale = _scale(t, b.opens, b.at, OPEN_S, ease_out_back)
            if scale > 0:
                _grow_onto(layer, sprites[b], (b.x, b.y), scale)
        frame.alpha_composite(layer)
        yield frame.convert("RGB").tobytes()
    yield clear.tobytes()  # end exactly on the clear frame


def _grow_onto(canvas: Image.Image, sprite: Image.Image, centre: Point, scale: float) -> None:
    """`sprite` at `scale` of its size, centred on `centre`."""
    side = max(1, round(sprite.width * max(0.05, scale)))
    shown = sprite.resize((side, side), Image.Resampling.BILINEAR)
    _over(canvas, shown, (round(centre[0] - side / 2), round(centre[1] - side / 2)))


def _over(canvas: Image.Image, sprite: Image.Image, at: tuple[int, int]) -> None:
    """alpha_composite `sprite` at `at`, even partly off the canvas (it needs both on it)."""
    x, y = at
    left, top = max(0, -x), max(0, -y)
    right = min(sprite.width, canvas.width - x)
    bottom = min(sprite.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(sprite.crop((left, top, right, bottom)), (x + left, y + top))


def render_part1(
    original: Image.Image, stages: list[Image.Image], mascot: encode.Still | None = None
) -> bytes:
    """Part 1 MP4: a sakura branch drawn across, blossoming, revealing the clear image, then
    erased the way it was drawn."""
    options = encode.Part1Options(crf=CRF, mascot=mascot)
    return encode.encode_part1(frames(original, stages), original.size, options)
