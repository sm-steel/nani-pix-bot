from typing import cast

from PIL import Image

from nani_pix_bot.services.cosmetics import frames, night_frames

GREEN = (10, 200, 30)


def _rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    return cast(tuple[int, int, int], image.getpixel(xy))


def _band_pixels(image: Image.Image, depth: int) -> list[tuple[int, int, int]]:
    w, h = image.size
    return [
        _rgb(image, (x, y))
        for x in range(w)
        for y in range(h)
        if min(x, y, w - 1 - x, h - 1 - y) < depth
    ]


def test_ofuda_keeps_size_and_leaves_the_middle() -> None:
    out = night_frames.ofuda_strip(Image.new("RGB", (400, 240), GREEN))
    assert out.size == (400, 240)
    assert _rgb(out, (200, 120)) == GREEN


def test_ofuda_is_registered() -> None:
    assert frames.FRAMES["ofuda_strip"] is night_frames.ofuda_strip


def test_ofuda_band_is_paper_with_red_seals_and_black_ink() -> None:
    out = night_frames.ofuda_strip(Image.new("RGB", (200, 120), GREEN))
    band = _band_pixels(out, 6)
    paperish = [p for p in band if p[0] > 200 and p[1] > 190]
    assert len(paperish) > len(band) * 0.5
    assert any(p[0] > 150 and p[1] < 80 for p in band)
    assert any(max(p) < 60 for p in band)


def test_ofuda_is_deterministic() -> None:
    image = Image.new("RGB", (300, 180), GREEN)
    assert night_frames.ofuda_strip(image).tobytes() == night_frames.ofuda_strip(image).tobytes()


def test_ofuda_handles_portrait_and_tiny_images() -> None:
    assert night_frames.ofuda_strip(Image.new("RGB", (120, 300), GREEN)).size == (120, 300)
    assert night_frames.ofuda_strip(Image.new("RGB", (20, 12), GREEN)).size == (20, 12)
