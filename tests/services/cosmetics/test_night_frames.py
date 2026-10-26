from typing import cast

from PIL import Image, ImageChops, ImageStat

from nani_pix_bot.services.cosmetics import frames, night_frames, paper_frames

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


def test_bone_border_keeps_size_and_leaves_the_middle() -> None:
    out = night_frames.bone_border(Image.new("RGB", (400, 240), GREEN))
    assert out.size == (400, 240)
    assert _rgb(out, (200, 120)) == GREEN


def test_bone_border_is_registered() -> None:
    assert frames.FRAMES["bone_border"] is night_frames.bone_border


def test_bone_border_band_has_ivory_bones_on_night() -> None:
    out = night_frames.bone_border(Image.new("RGB", (200, 120), GREEN))
    band = _band_pixels(out, 6)
    assert any(min(p) > 200 for p in band)
    assert any(max(p) < 60 for p in band)


def test_bone_border_ends_all_four_corners_the_same_way() -> None:
    w, h = 400, 240
    out = night_frames.bone_border(Image.new("RGB", (w, h), GREEN))
    b = paper_frames.border_for((w, h))
    size = (b * 2, b * 2 // 3)  # the bone end, clear of the deckled inner edge
    top = out.crop((0, 0, size[0], size[1]))
    bottom = out.crop((0, h - size[1], size[0], h))
    # a bone end sits in the corner: every channel reaches ivory
    assert min(hi for _, hi in ImageStat.Stat(top).extrema) > 200
    pairs = [
        (top, out.crop((w - size[0], 0, w, size[1])).transpose(Image.Transpose.FLIP_LEFT_RIGHT)),
        (
            bottom,
            out.crop((w - size[0], h - size[1], w, h)).transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        ),
    ]
    for corner, mirrored in pairs:
        diff = ImageChops.difference(corner, mirrored).convert("L")
        assert ImageStat.Stat(diff).mean[0] < 12


def test_bone_border_is_deterministic() -> None:
    image = Image.new("RGB", (300, 180), GREEN)
    assert night_frames.bone_border(image).tobytes() == night_frames.bone_border(image).tobytes()


def test_bone_border_handles_portrait_and_tiny_images() -> None:
    assert night_frames.bone_border(Image.new("RGB", (120, 300), GREEN)).size == (120, 300)
    assert night_frames.bone_border(Image.new("RGB", (20, 12), GREEN)).size == (20, 12)
