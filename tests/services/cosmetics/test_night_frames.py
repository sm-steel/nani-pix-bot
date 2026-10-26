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
    assert min(hi for _, hi in ImageStat.Stat(bottom).extrema) > 200
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


def test_cobweb_is_registered() -> None:
    assert frames.FRAMES["cobweb"] is night_frames.cobweb


def test_cobweb_leaves_the_middle_and_nanis_corner() -> None:
    image = Image.new("RGB", (400, 240), (20, 24, 30))
    out = night_frames.cobweb(image)
    assert out.size == image.size
    assert _rgb(out, (200, 120)) == _rgb(image, (200, 120))
    box = (round(400 * 0.85), round(240 * 0.85), 400, 240)
    assert out.crop(box).tobytes() == image.crop(box).tobytes()


def test_cobweb_threads_in_three_corners() -> None:
    w, h = 400, 240
    image = Image.new("RGB", (w, h), (20, 24, 30))
    out = night_frames.cobweb(image)
    reach = round(min(w, h) * night_frames.WEB_RATIO)
    for x, y in ((0, 0), (w - reach, 0), (0, h - reach)):
        box = (x, y, x + reach, y + reach)
        a, b = image.crop(box).convert("L"), out.crop(box).convert("L")
        lighter = ImageChops.subtract(b, a).point(lambda v: 255 if v > 25 else 0)
        assert lighter.histogram()[255] > 20


def test_cobweb_is_deterministic() -> None:
    image = Image.new("RGB", (300, 180), GREEN)
    assert night_frames.cobweb(image).tobytes() == night_frames.cobweb(image).tobytes()


def test_cobweb_handles_portrait_and_tiny_images() -> None:
    assert night_frames.cobweb(Image.new("RGB", (120, 300), GREEN)).size == (120, 300)
    assert night_frames.cobweb(Image.new("RGB", (20, 12), GREEN)).size == (20, 12)


def test_cobweb_fades_out_towards_the_corner() -> None:
    image = Image.new("RGB", (400, 240), (20, 24, 30))
    out = night_frames.cobweb(image)
    reach = round(240 * night_frames.WEB_RATIO)
    diff = ImageChops.subtract(out, image).convert("L")
    near = diff.crop((0, 0, reach // 5, reach // 5))
    far = diff.crop((reach // 2, 0, reach, reach // 2))
    assert ImageStat.Stat(near).mean[0] * 3 < ImageStat.Stat(far).mean[0]
    assert ImageStat.Stat(diff).extrema[0][1] < 130  # translucent, never solid thread


def test_cobweb_corners_are_not_mirror_images() -> None:
    w, h = 400, 240
    out = night_frames.cobweb(Image.new("RGB", (w, h), (20, 24, 30)))
    r = round(min(w, h) * night_frames.WEB_RATIO)
    tl = out.crop((0, 0, r, r))
    tr = out.crop((w - r, 0, w, r)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    bl = out.crop((0, h - r, r, h)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    for a, b in ((tl, tr), (tl, bl), (tr, bl)):
        assert ImageStat.Stat(ImageChops.difference(a, b).convert("L")).mean[0] > 3
