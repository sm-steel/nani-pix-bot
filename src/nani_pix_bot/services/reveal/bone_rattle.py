"""The "bone_rattle" win overlay for the reveal ending (seasons spec §7): a little
skeleton dances on the winner's badge, jaw clacking, as a qtrle/argb .mov like
spirits.bat_burst_clip. Every part is committed art (assets/sprites/skeleton/): the
limbs are the long-bone sprite rotated per segment, plus a hand or a foot at the end.
Runs in the reveal worker: module-level functions, seeded, no logging."""

import math
import random
from dataclasses import dataclass
from functools import cache, lru_cache
from typing import NamedTuple

from PIL import Image, ImageOps

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import sprite_or_none
from nani_pix_bot.services.reveal.overlay_canvas import SS, clip_size, render_layers

RATTLE_FRAMES = 56  # the ending is 60 frames; the skeleton fades out just before it ends
RATTLE_SEED = 61
DANCE_PERIOD = 16  # frames per dance cycle
CLACK_PERIOD = 6  # frames per jaw open-and-shut
JAW_OPEN = 0.25  # how far the jaw drops, in skull heights
SKELETON_HEIGHT = 2.2  # in badge heights, standing on the badge's top edge
STAND_X = 3.75  # in badge heights from the left
SWAY = 8.0  # degrees the ribcage rocks each way
JITTER = 1  # clip pixels each part shakes by: the rattle

SKULL, JAW, RIBS, PELVIS, BONE, HAND, FOOT = (
    f"skeleton/{n}.png" for n in ("skull", "jaw", "ribcage", "pelvis", "bone", "hand", "foot")
)
BONE_ASPECT = 99 / 256  # height over width of the long-bone sprite
BONE_REACH = 0.82  # joint-to-joint share of the sprite's width: the knobs stick out
MISSING = "the skeleton loses that part"

# The rig, in skeleton heights: x from the spine, y up from the ground.
PELVIS_AT, PELVIS_H = 0.395, 0.115
HIP = (0.04, 0.385)
THIGH, SHIN = 0.17, 0.16
FOOT_H, FOOT_ANKLE = 0.09, (0.28, 0.12)  # the ankle's place on the foot art
FOOT_TURN = 0.4  # how much of the shin's angle the foot follows
RIBS_PIVOT, RIBS_H = (0.0, 0.455), 0.27
RIBS_BASE = 0.9  # the share of the ribcage art that is above its pivot
SHOULDER = (0.085, 0.215)  # from the ribcage pivot
NECK = 0.24  # from the ribcage pivot
SKULL_H, SKULL_LIFT = 0.2, 0.02
JAW_H = SKULL_H * 96 / 244  # the jaw art at the skull's scale
JAW_HOOK = 0.3  # the share of the jaw art that tucks under the skull when shut
UPPER_ARM, FOREARM, HAND_H = 0.15, 0.14, 0.11
HAND_WRIST = (0.5, 0.95)
BODY_BOB = 0.008
PARTS = ("legs", "pelvis", "ribs", "arms", "jaw", "skull")  # each shakes on its own

Point = tuple[float, float]


class _Part(NamedTuple):
    """A sprite, its height in skeleton heights and its pivot (shares of its size from
    its top left): the spot that turns and that lands on the joint."""

    name: str
    height: float
    pivot: Point


PART_SKULL = _Part(SKULL, SKULL_H, (0.5, 1.0))
PART_JAW = _Part(JAW, JAW_H, (0.5, JAW_HOOK))
PART_RIBS = _Part(RIBS, RIBS_H, (0.5, RIBS_BASE))
PART_PELVIS = _Part(PELVIS, PELVIS_H, (0.5, 0.5))
PART_HAND = _Part(HAND, HAND_H, HAND_WRIST)
PART_FOOT = _Part(FOOT, FOOT_H, FOOT_ANKLE)


@dataclass(frozen=True)
class _Limb:
    """A two-segment limb's dance: each angle is (base, amplitude, phase)."""

    upper: tuple[float, float, float]
    bend: tuple[float, float, float]


@dataclass(frozen=True)
class _Pose:
    sway: float  # ribcage rotation, degrees counter-clockwise
    bob: float  # lift of the whole skeleton, in skeleton heights
    jaw: float  # how far the jaw hangs open, in skull heights
    arms: tuple[Point, ...]  # (upper, forearm) per side: left, right
    legs: tuple[Point, ...]  # (thigh, shin) per side: left, right
    shake: tuple[Point, ...]  # one (dx, dy) in layer pixels per PARTS entry


@cache
def _dance() -> tuple[tuple[_Limb, ...], tuple[_Limb, ...]]:
    """(arms, legs), each (left, right): the seeded phases of the dance."""
    rng = random.Random(RATTLE_SEED)  # noqa: S311 - decoration, seeded for a reproducible look

    def limb(upper: Point, bend: Point) -> _Limb:
        return _Limb(
            (upper[0], upper[1], rng.uniform(0, math.tau)),
            (bend[0], bend[1], rng.uniform(0, math.tau)),
        )

    arms = tuple(limb((80, 50), (20, 50)) for _ in range(2))
    legs = tuple(limb((18, 12), (-10, 10)) for _ in range(2))
    return arms, legs


def _angles(limb: _Limb, f: int) -> Point:
    """(upper, lower) in degrees from hanging straight down, positive outwards."""
    cycle = math.tau * f / DANCE_PERIOD
    upper = limb.upper[0] + limb.upper[1] * math.sin(cycle + limb.upper[2])
    return upper, upper + limb.bend[0] + limb.bend[1] * math.sin(cycle + limb.bend[2])


def _jaw_open(f: int) -> float:
    """How far the jaw hangs, in skull heights: shut at f = 0, wide open mid-period."""
    return JAW_OPEN * (0.5 - 0.5 * math.cos(math.tau * f / CLACK_PERIOD))


def _pose(f: int) -> _Pose:
    arms, legs = _dance()
    rng = random.Random(RATTLE_SEED * 1000 + f)  # noqa: S311 - decoration, seeded
    return _Pose(
        sway=SWAY * math.sin(math.tau * f / DANCE_PERIOD),
        bob=BODY_BOB * abs(math.sin(math.tau * f / (DANCE_PERIOD / 2))),
        jaw=_jaw_open(f),
        arms=tuple(_angles(a, f) for a in arms),
        legs=tuple(_angles(leg, f) for leg in legs),
        shake=tuple(
            (rng.randint(-JITTER, JITTER) * SS, rng.randint(-JITTER, JITTER) * SS) for _ in PARTS
        ),
    )


@lru_cache(maxsize=512)
def _rotated(
    name: str, height: int, degrees: int, flip: bool
) -> tuple[Image.Image, tuple[int, int]] | None:
    """The sprite at `height`, mirrored if `flip`, turned `degrees` counter-clockwise,
    with its size before the turn. Whole degrees only, so the cache stays small."""
    art = sprite_or_none(name, height, MISSING)
    if art is None:
        return None
    if flip:
        art = ImageOps.mirror(art)
    return art.rotate(degrees, Image.Resampling.BICUBIC, expand=True), art.size


def _turn(v: Point, degrees: float) -> Point:
    """`v` (x right, y up) turned counter-clockwise."""
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return v[0] * c - v[1] * s, v[0] * s + v[1] * c


class _Skeleton:
    """Paints one pose onto a layer: `unit` pixels per skeleton height, the spine at
    x = `centre` and the ground at y = `ground`."""

    def __init__(self, layer: Image.Image, unit: float, centre: float, ground: float) -> None:
        self.layer, self.unit, self.centre, self.ground = layer, unit, centre, ground

    def at(self, x: float, y: float, shake: Point = (0, 0)) -> Point:
        """Layer pixels for a rig point (skeleton heights)."""
        return self.centre + x * self.unit + shake[0], self.ground - y * self.unit + shake[1]

    def put(
        self,
        part: _Part,
        where: Point,
        degrees: float = 0,
        flip: bool = False,
    ) -> None:
        """Paste the sprite, turned about `pivot` (shares of its size from its top left)
        so that the pivot lands on `where`."""
        h = max(1, round(part.height * self.unit))
        turn = round(degrees)
        turned = _rotated(part.name, h, turn, flip)
        if turned is None:
            return
        art, (width, height_px) = turned
        px = (1 - part.pivot[0] if flip else part.pivot[0]) * width - width / 2
        py = part.pivot[1] * height_px - height_px / 2
        tx, ty = _turn((px, -py), turn)
        self.layer.alpha_composite(
            art, (round(where[0] - art.width / 2 - tx), round(where[1] - art.height / 2 + ty))
        )

    def bone(self, a: Point, b: Point) -> None:
        """A long bone from joint `a` to joint `b` (layer pixels)."""
        length = math.hypot(b[0] - a[0], b[1] - a[1]) / BONE_REACH
        degrees = math.degrees(math.atan2(a[1] - b[1], b[0] - a[0]))
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        self.put(_Part(BONE, length * BONE_ASPECT / self.unit, (0.5, 0.5)), mid, degrees)

    def limb(self, start: Point, lengths: Point, side: int, angles: Point) -> Point:
        """Two bones from `start`; returns the end joint, in layer pixels."""
        joint = start
        for length, angle in zip(lengths, angles, strict=True):
            r = math.radians(angle)
            reach = length * self.unit
            end = (joint[0] + side * math.sin(r) * reach, joint[1] + math.cos(r) * reach)
            self.bone(joint, end)
            joint = end
        return joint


def _paint_skeleton(sk: _Skeleton, pose: _Pose) -> None:
    """Legs, pelvis, ribcage, arms, jaw, skull: the jaw goes under the skull so it clacks
    from beneath."""
    shake = dict(zip(PARTS, pose.shake, strict=True))
    lift = pose.bob

    for side, (thigh, shin) in zip((-1, 1), pose.legs, strict=True):
        hip = sk.at(side * HIP[0], HIP[1] + lift, shake["legs"])
        ankle = sk.limb(hip, (THIGH, SHIN), side, (thigh, shin))
        sk.put(PART_FOOT, ankle, -side * shin * FOOT_TURN, flip=side < 0)
    sk.put(PART_PELVIS, sk.at(0, PELVIS_AT + lift, shake["pelvis"]))

    pivot = sk.at(RIBS_PIVOT[0], RIBS_PIVOT[1] + lift, shake["ribs"])
    sk.put(PART_RIBS, pivot, pose.sway)

    def on_ribs(x: float, y: float, own: Point = (0, 0)) -> Point:
        tx, ty = _turn((x, y), pose.sway)
        return pivot[0] + tx * sk.unit + own[0], pivot[1] - ty * sk.unit + own[1]

    for side, (upper, lower) in zip((-1, 1), pose.arms, strict=True):
        shoulder = on_ribs(side * SHOULDER[0], SHOULDER[1], shake["arms"])
        wrist = sk.limb(shoulder, (UPPER_ARM, FOREARM), side, (upper, lower))
        r = math.radians(lower)
        fingers = math.degrees(math.atan2(math.cos(r), side * math.sin(r)))
        sk.put(PART_HAND, wrist, fingers - 90, flip=side < 0)

    skull = on_ribs(0, NECK + SKULL_LIFT, shake["skull"])
    hang = (
        skull[0] - shake["skull"][0],
        skull[1] - shake["skull"][1] + pose.jaw * SKULL_H * sk.unit,
    )
    own = shake["jaw"]
    sk.put(PART_JAW, (hang[0] + own[0], hang[1] + own[1]))
    sk.put(PART_SKULL, skull)


def rattle_frames(height: int) -> bytes:
    size = clip_size(height)
    unit = SKELETON_HEIGHT * height * SS
    span = (round(1.3 * unit), round(1.15 * unit))
    ground = (size[1] - height) * SS
    origin = (round(STAND_X * height * SS - span[0] / 2), round(ground - 1.05 * unit))

    def paint_frame(big: Image.Image, f: int, alpha: int) -> None:
        if alpha == 255:  # no fade to apply: paint straight onto the frame
            sk = _Skeleton(big, unit, origin[0] + span[0] / 2, origin[1] + 1.05 * unit)
            _paint_skeleton(sk, _pose(f))
            return
        layer = Image.new("RGBA", span, (0, 0, 0, 0))
        _paint_skeleton(_Skeleton(layer, unit, span[0] / 2, 1.05 * unit), _pose(f))
        layer.putalpha(layer.getchannel("A").point([round(v * alpha / 255) for v in range(256)]))
        big.alpha_composite(layer, origin)

    return render_layers(size, height, RATTLE_FRAMES, paint_frame)


def bone_rattle_clip(height: int) -> tuple[bytes, tuple[int, int]]:
    """A skeleton dances on the badge, its jaw clacking, then fades away."""
    size = clip_size(height)
    return encode.encode_rgba_clip(rattle_frames(height), size), size
