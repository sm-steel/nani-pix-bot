import io

import pytest
from PIL import Image

from nani_pix_bot.services.reveal import celebration, encode


def _avatar() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 160), (200, 80, 80)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.parametrize("avatar", [None, "jpeg"])
@pytest.mark.parametrize("handle", ["@UnstableFractal", "Яна", "x"])
def test_badge_is_rgba_and_sized_by_height(avatar, handle) -> None:
    badge = celebration.make_badge(_avatar() if avatar else None, handle, 86)
    assert badge.mode == "RGBA"
    assert 86 <= badge.height <= 86 * 2
    assert badge.getbbox() is not None


def test_longer_handle_makes_a_wider_badge() -> None:
    short = celebration.make_badge(None, "@ab", 86)
    long = celebration.make_badge(None, "@a_much_longer_handle", 86)
    assert long.width > short.width


def test_badge_frames_are_24_rgba_frames() -> None:
    badge = celebration.make_badge(None, "@abc", 60)
    raw, size = celebration.badge_frames(badge)
    assert len(raw) == 24 * size[0] * size[1] * 4


def test_badge_height_scales_with_the_frame() -> None:
    assert celebration.badge_height((1280, 632)) == 86
    assert celebration.badge_height((320, 180)) == 72


@pytest.mark.ffmpeg
def test_confetti_clip_is_a_decodable_mov() -> None:
    clip, size = celebration.confetti_clip(60)
    assert clip[4:8] == b"ftyp" or b"moov" in clip[:4096]
    assert size[0] > 0
    assert size[1] > 60


@pytest.mark.ffmpeg
def test_encode_rgba_clip_makes_a_mov() -> None:
    size = (16, 12)
    clip = encode.encode_rgba_clip(bytes([255, 0, 0, 128]) * (16 * 12 * 3), size)
    assert b"moov" in clip[:4096] or b"moov" in clip[-4096:]


def test_label_is_shown_verbatim_without_adding_an_at_sign() -> None:
    plain = celebration.make_badge(None, "Яна", 86)
    prefixed = celebration.make_badge(None, "@Яна", 86)
    assert plain.width < prefixed.width
    assert plain.tobytes() != prefixed.tobytes()
