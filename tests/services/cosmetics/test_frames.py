from io import BytesIO
from typing import cast

from PIL import Image

from nani_pix_bot.services.cosmetics import frames, paper_frames


def _png(size=(200, 120), color=(10, 200, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_simple_border_keeps_size_and_paints_the_edge() -> None:
    out = Image.open(BytesIO(frames.apply_frame(_png(), "simple_border")))
    assert out.size == (200, 120)
    assert out.getpixel((0, 0)) != (10, 200, 30)
    assert out.getpixel((100, 60)) == (10, 200, 30)


def _rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    return cast(tuple[int, int, int], image.getpixel(xy))


def test_paper_keeps_size_paints_cream_paper_and_leaves_the_middle() -> None:
    green = (10, 200, 30)
    out = Image.open(BytesIO(frames.apply_frame(_png((400, 240), green), "paper")))
    assert out.size == (400, 240)
    r, g, b = _rgb(out, (1, 1))  # cream, not green
    assert r > 180
    assert g > 170
    assert b > 150
    assert _rgb(out, (200, 120)) == green


def test_paper_border_is_three_percent_of_the_short_side() -> None:
    green = (10, 200, 30)
    out = Image.open(BytesIO(frames.apply_frame(_png((1000, 600), green), "paper")))
    border = round(600 * paper_frames.BORDER_RATIO)  # 18 px
    assert _rgb(out, (border // 3, 300))[1] < 240  # paper at the very edge
    inside = _rgb(out, (border * 3, 300))  # past the deckle and the shadow
    assert inside == green


def test_paper_casts_a_shadow_just_inside_its_edge() -> None:
    green = (10, 200, 30)
    out = Image.open(BytesIO(frames.apply_frame(_png((1000, 600), green), "paper")))
    border = round(600 * paper_frames.BORDER_RATIO)
    near = _rgb(out, (border + border // 2 + 2, 300))
    assert near[1] < 200  # darker than the untouched green


def test_paper_is_deterministic() -> None:
    png = _png((300, 200))
    assert frames.apply_frame(png, "paper") == frames.apply_frame(png, "paper")


def test_unknown_frame_returns_the_image_unchanged() -> None:
    png = _png()
    assert frames.apply_frame(png, "nope") == png
