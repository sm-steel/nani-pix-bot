from io import BytesIO

from PIL import Image

from nani_pix_bot.services.cosmetics import frames


def _png(size=(200, 120), color=(10, 200, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_simple_border_keeps_size_and_paints_the_edge() -> None:
    out = Image.open(BytesIO(frames.apply_frame(_png(), "simple_border")))
    assert out.size == (200, 120)
    assert out.getpixel((0, 0)) != (10, 200, 30)
    assert out.getpixel((100, 60)) == (10, 200, 30)


def test_unknown_frame_returns_the_image_unchanged() -> None:
    png = _png()
    assert frames.apply_frame(png, "nope") == png
