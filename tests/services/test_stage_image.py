from io import BytesIO

from PIL import Image

from nani_pix_bot.services import stage_image


def _png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(out, format="PNG")
    return out.getvalue()


def test_the_drawn_image_comes_back_as_png() -> None:
    out = stage_image.redraw_or_keep(_png(), lambda im: im.convert("L"), "frame", "k")
    assert Image.open(BytesIO(out)).mode == "L"


def test_a_failing_draw_keeps_the_png_and_logs_the_kind_and_key(log_records) -> None:
    def boom(_image: Image.Image) -> Image.Image:
        raise ValueError("bad")

    png = _png()
    assert stage_image.redraw_or_keep(png, boom, "style", "moonlit") == png
    assert [(r.level, r.extra["kind"], r.extra["key"]) for r in log_records] == [
        ("ERROR", "style", "moonlit")
    ]
