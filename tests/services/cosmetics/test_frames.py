from io import BytesIO
from typing import cast

from PIL import Image

from nani_pix_bot.services.cosmetics import frames, gold_frame, paper_frames


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


def _gold(size=(1000, 600), color=(10, 200, 30)) -> Image.Image:
    return Image.open(BytesIO(frames.apply_frame(_png(size, color), "gold_washi")))


def test_gold_washi_keeps_size_and_leaves_the_middle() -> None:
    out = _gold()
    assert out.size == (1000, 600)
    assert _rgb(out, (500, 300)) == (10, 200, 30)


def test_gold_washi_band_is_gold_not_paper() -> None:
    """Gold: red over green over blue, clearly saturated — along the whole band."""
    out = _gold()
    for x in range(40, 960, 37):
        r, g, b = _rgb(out, (x, 3))
        assert r >= g > b
        assert r - b > 60


def test_gold_washi_shines() -> None:
    """The metal sweeps from dark to bright along the band, not one flat colour."""
    out = _gold()
    brightness = [sum(_rgb(out, (x, 3))) for x in range(40, 960, 5)]
    assert max(brightness) - min(brightness) > 150


def test_gold_washi_has_a_blossom_with_a_red_centre_on_every_corner() -> None:
    out = _gold()
    border = paper_frames.border_for(out.size)
    half = round(border * gold_frame._BLOSSOM_RATIO) // 2
    inset = half + max(2, border // 6)
    for x, y in (
        (inset, inset),
        (1000 - inset, inset),
        (inset, 600 - inset),
        (1000 - inset, 600 - inset),
    ):
        r, g, _b = _rgb(out, (x, y))
        assert r > 150
        assert g < 110  # the red centre, not gold or green


def test_gold_washi_is_deterministic() -> None:
    png = _png((300, 200))
    assert frames.apply_frame(png, "gold_washi") == frames.apply_frame(png, "gold_washi")


def _band(out: Image.Image, depth: int) -> list[tuple[int, int, int]]:
    """Every pixel within `depth` of the top and bottom edges."""
    w, h = out.size
    rows = [*range(depth), *range(h - depth, h)]
    return [_rgb(out, (x, y)) for y in rows for x in range(w)]


def _washi(size=(1000, 600), color=(10, 200, 30)) -> Image.Image:
    return Image.open(BytesIO(frames.apply_frame(_png(size, color), "washi")))


def test_washi_keeps_size_and_leaves_the_middle() -> None:
    out = _washi()
    assert out.size == (1000, 600)
    assert _rgb(out, (500, 300)) == (10, 200, 30)


def test_washi_is_pink_paper_with_white_fibres_and_deep_pink_petals() -> None:
    out = _washi()
    band = _band(out, depth=6)  # inside the paper everywhere, past the ragged edge
    pinkish = [p for p in band if p[0] > p[1] + 15 and p[0] > 200]
    white = [p for p in band if min(p) > 238]
    petals = [p for p in band if p[0] > 190 and p[1] < 150]
    assert len(pinkish) > len(band) * 0.6  # the paper itself is pink
    assert white  # unryū fibres
    assert petals  # pressed sakura petals


def test_washi_is_not_plain_paper() -> None:
    png = _png((600, 400))
    assert frames.apply_frame(png, "washi") != frames.apply_frame(png, "paper")


def test_washi_is_deterministic() -> None:
    png = _png((300, 200))
    assert frames.apply_frame(png, "washi") == frames.apply_frame(png, "washi")


def test_unknown_frame_returns_the_image_unchanged() -> None:
    png = _png()
    assert frames.apply_frame(png, "nope") == png


def test_a_frame_that_raises_posts_the_stage_unframed_and_logs(monkeypatch, log_records) -> None:
    def boom(image: Image.Image) -> Image.Image:
        raise ValueError("bad art")

    monkeypatch.setattr(frames, "FRAMES", {"boom": boom})
    png = _png()
    assert frames.apply_frame(png, "boom") == png
    assert any(r.level == "ERROR" and r.extra["frame_key"] == "boom" for r in log_records)
