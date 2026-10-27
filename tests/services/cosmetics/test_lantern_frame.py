from typing import cast

import pytest
from PIL import Image

from nani_pix_bot.services import mascot, sprites
from nani_pix_bot.services.cosmetics import frames, lantern_frame
from nani_pix_bot.services.mascot import Pose

GREEN = (10, 200, 30)


def _rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    return cast(tuple[int, int, int], image.getpixel(xy))


def _is_red_orange(p: tuple[int, int, int]) -> bool:
    return p[0] > 180 and p[1] < 140 and p[2] < 100


def _red_count(image: Image.Image, top: float = 0.18) -> int:
    w, h = image.size
    return sum(_is_red_orange(_rgb(image, (x, y))) for x in range(w) for y in range(int(h * top)))


def _blobs(image: Image.Image) -> int:
    """Runs of adjacent columns holding red-orange pixels in the top band."""
    w, h = image.size
    cols = [any(_is_red_orange(_rgb(image, (x, y))) for y in range(int(h * 0.3))) for x in range(w)]
    return sum(1 for x, on in enumerate(cols) if on and (x == 0 or not cols[x - 1]))


def test_lantern_string_keeps_size_and_leaves_the_lower_middle() -> None:
    out = lantern_frame.lantern_string(Image.new("RGB", (960, 720), GREEN))
    assert out.size == (960, 720)
    assert _rgb(out, (480, int(720 * 0.6))) == GREEN


def test_lantern_string_is_registered() -> None:
    assert frames.FRAMES["lantern_string"] is lantern_frame.lantern_string


def test_lanterns_hang_along_the_top() -> None:
    out = lantern_frame.lantern_string(Image.new("RGB", (960, 720), GREEN))
    assert _red_count(out) > 300


def test_lantern_count_grows_with_width() -> None:
    narrow = lantern_frame.lantern_string(Image.new("RGB", (960, 720), GREEN))
    wide = lantern_frame.lantern_string(Image.new("RGB", (1920, 720), GREEN))
    assert _blobs(wide) > _blobs(narrow)


def test_lantern_string_is_deterministic() -> None:
    src = Image.new("RGB", (640, 360), GREEN)
    a = lantern_frame.lantern_string(src)
    b = lantern_frame.lantern_string(src)
    assert a.tobytes() == b.tobytes()


def test_the_rightmost_lantern_stays_left_of_nani() -> None:
    size = (900, 160)  # so short that Nani's box reaches the top
    x, y, width, _ = mascot.box(size, Pose.NOPE)
    assert width > 0
    assert y < 0.2 * size[1]
    out = lantern_frame.lantern_string(Image.new("RGB", size, GREEN))
    for px in range(x, size[0]):
        for py in range(int(size[1] * 0.3)):
            assert not _is_red_orange(_rgb(out, (px, py)))


@pytest.mark.parametrize("size", [(360, 640), (1, 1), (20, 12), (40, 300), (300, 40)])
def test_lantern_string_handles_portrait_and_tiny_images(size: tuple[int, int]) -> None:
    assert lantern_frame.lantern_string(Image.new("RGB", size, GREEN)).size == size


def test_a_missing_sprite_still_returns_the_stage_and_logs_once(monkeypatch, caplog) -> None:
    def gone(*_a, **_k):
        raise FileNotFoundError("chochin.png")

    monkeypatch.setattr(sprites, "sprite_height", gone)
    lantern_frame._hung.cache_clear()
    src = Image.new("RGB", (640, 360), GREEN)
    try:
        first = lantern_frame.lantern_string(src)
        lantern_frame.lantern_string(src)
    finally:
        lantern_frame._hung.cache_clear()
    assert first.size == src.size
    assert _red_count(first) == 0
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert lantern_frame.SPRITE in errors[0].getMessage()


def test_the_cord_runs_dark_along_the_top() -> None:
    out = lantern_frame.lantern_string(Image.new("RGB", (960, 720), (220, 220, 220)))
    # x=320 lies between two lanterns, so only the cord can be dark there
    assert any(max(_rgb(out, (320, y))) < 90 for y in range(40))


def test_each_lantern_casts_a_warm_glow_below_it() -> None:
    grey = (120, 120, 120)
    out = lantern_frame.lantern_string(Image.new("RGB", (960, 720), grey))
    r, g, b = _rgb(out, (160, 88))  # just under the first lantern
    assert r > grey[0] + 8  # lit, not just left alone
    assert r > g
    assert g > b  # and warm: red over green over blue
