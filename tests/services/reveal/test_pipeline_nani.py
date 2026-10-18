"""Nani Pix in the reveal video (issue #461): the cast pose over the effect part, the
outcome's pose over the ending. A flat picture keeps every frame's background the same,
so Nani's corner can be compared with a reference composited over that colour."""

import io

import pytest
from PIL import Image, ImageChops, ImageStat

from nani_pix_bot.models.enums import PixelAlgorithm, RevealEffect
from nani_pix_bot.services import mascot
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.reveal import encode, pipeline
from nani_pix_bot.services.reveal.effects import EFFECTS

from .ffprobe_helpers import decode_frames

WIDTHS = (64, 80, 128, 192, 512)
SIZE = (640, 360)
FLAT = (90, 140, 200)


def _flat_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", SIZE, FLAT).save(buf, "PNG")
    return buf.getvalue()


def _distance(frame: Image.Image, pose: Pose) -> float:
    """How far `frame`'s Nani corner is from `pose` composited over the flat colour."""
    x, y, w, h = mascot.box(SIZE, pose)
    expected = mascot.compose(Image.new("RGB", SIZE, FLAT), pose).crop((x, y, x + w, y + h))
    diff = ImageChops.difference(frame.crop((x, y, x + w, y + h)), expected)
    return sum(ImageStat.Stat(diff).mean) / 3


def _shows(frame: Image.Image, pose: Pose, other: Pose) -> bool:
    return _distance(frame, pose) < _distance(frame, other)


@pytest.mark.ffmpeg
@pytest.mark.parametrize("effect", [RevealEffect.IRIS, RevealEffect.GLITCH])
def test_the_effect_part_shows_cast_and_the_ending_the_outcome(effect) -> None:
    png = _flat_png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, effect)

    frames = decode_frames(pipeline.finish(pregen, png, None, Pose.WIN), SIZE)

    assert _shows(frames[3], Pose.CAST, Pose.WIN)
    assert _shows(frames[-1], Pose.WIN, Pose.CAST)


@pytest.mark.ffmpeg
def test_an_unsolved_ending_shows_oops() -> None:
    png = _flat_png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)

    frames = decode_frames(pipeline.finish(pregen, png, None, Pose.OOPS), SIZE)

    assert _shows(frames[-1], Pose.OOPS, Pose.WIN)


@pytest.mark.ffmpeg
def test_a_reveal_rendered_before_nani_still_ends_with_her() -> None:
    """A slot pre-rendered by the previous release has no Nani in part 1; its ending,
    rendered at reveal time, still gets the outcome's pose."""
    png = _flat_png()
    original, stages = pipeline.prep(png, PixelAlgorithm.MEDIAN, WIDTHS)
    part1_ts, join_offset = encode.to_ts(EFFECTS[RevealEffect.IRIS](original, stages, None))
    old = pipeline.Pregen(part1_ts, join_offset, render_ms=0)

    frames = decode_frames(pipeline.finish(old, png, None, Pose.OOPS), SIZE)

    assert _shows(frames[-1], Pose.OOPS, Pose.CAST)


@pytest.mark.ffmpeg
def test_the_winner_badge_and_nani_share_the_ending() -> None:
    png = _flat_png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    badge = pipeline.Badge(avatar=None, handle="@" + "w" * 120)

    frames = decode_frames(pipeline.finish(pregen, png, badge, Pose.WIN), SIZE)

    assert _shows(frames[-1], Pose.WIN, Pose.CAST)


@pytest.mark.ffmpeg
def test_a_cosmetic_reveal_preview_ends_like_a_won_game() -> None:
    clip = pipeline.preview_clip(
        _flat_png(), PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS, None
    )
    frames = decode_frames(clip, SIZE)
    assert _shows(frames[3], Pose.CAST, Pose.WIN)
    assert _shows(frames[-1], Pose.WIN, Pose.CAST)
