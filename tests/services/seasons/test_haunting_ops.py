import colorsys
from io import BytesIO

import pytest
from PIL import Image

from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services.seasons import haunting

RED = (255, 0, 0)
BLUE = (0, 0, 255)


def _image() -> Image.Image:
    """40x20, left half red, right half blue."""
    img = Image.new("RGB", (40, 20), RED)
    img.paste(Image.new("RGB", (20, 20), BLUE), (20, 0))
    return img


def _px(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    pixel = image.getpixel(xy)
    assert isinstance(pixel, tuple)
    r, g, b = pixel[:3]
    return r, g, b


def _hue(pixel: tuple[int, int, int]) -> int:
    return round(colorsys.rgb_to_hsv(*(c / 255 for c in pixel))[0] * 255)


def test_kitsune_mirror_swaps_left_and_right() -> None:
    out = haunting.twist_image(_image(), HauntTwist.KITSUNE_MIRROR)
    assert out.getpixel((0, 0)) == BLUE
    assert out.getpixel((39, 0)) == RED


def test_tanuki_shifts_the_hue() -> None:
    out = haunting.twist_image(_image(), HauntTwist.TANUKI_LEAF)
    expected = (_hue(RED) + haunting.TANUKI_HUE_STEPS) % 256
    assert abs(_hue(_px(out, (0, 0))) - expected) <= 2


def test_yurei_blends_a_shifted_copy() -> None:
    img = _image()
    dx = round(img.width * haunting.YUREI_OFFSET[0])
    out = haunting.twist_image(img, HauntTwist.YUREI_DOUBLE)
    # just right of the edge, within the offset: the shifted copy still shows red there
    x = 20 + dx - 1
    alpha = haunting.YUREI_ALPHA
    expected = tuple(round(b * (1 - alpha) + r * alpha) for b, r in zip(BLUE, RED, strict=True))
    got = _px(out, (x, 10))
    assert all(abs(g - e) <= 1 for g, e in zip(got, expected, strict=True))


def test_xray_is_an_inverted_negative() -> None:
    black = Image.new("RGB", (4, 4), (0, 0, 0))
    white = Image.new("RGB", (4, 4), (255, 255, 255))
    assert haunting.twist_image(black, HauntTwist.KYOKOTSU_XRAY).getpixel((0, 0)) == (
        haunting.XRAY_LIGHT
    )
    assert haunting.twist_image(white, HauntTwist.KYOKOTSU_XRAY).getpixel((0, 0)) == (
        haunting.XRAY_DARK
    )


@pytest.mark.parametrize("twist", list(HauntTwist))
def test_ops_keep_the_size(twist) -> None:
    img = _image()
    assert haunting.twist_image(img, twist).size == img.size


def _png(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def test_apply_twists_round_trips_png_bytes() -> None:
    out = Image.open(BytesIO(haunting.apply_twists(_png(_image()), (HauntTwist.KITSUNE_MIRROR,))))
    assert out.convert("RGB").getpixel((0, 0)) == BLUE


def test_no_twists_leave_the_png_alone() -> None:
    png = _png(_image())
    assert haunting.apply_twists(png, ()) is png


def test_boss_stage_image_applies_every_twist_in_order() -> None:
    expected = _image()
    for twist in haunting.BOSS_ORDER:
        expected = haunting.twist_image(expected, twist)
    out = Image.open(BytesIO(haunting.apply_twists(_png(_image()), haunting.BOSS_ORDER)))
    assert out.convert("RGB").tobytes() == expected.tobytes()
