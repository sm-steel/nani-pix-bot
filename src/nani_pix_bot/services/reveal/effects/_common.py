"""Helpers shared by the reveal effects."""

from PIL import Image

# BT.601 limited range, RGB 0..255 -> Y 16..235, Cb/Cr 16..240 (swscale's default for yuv420p)
_MY = (0.256788, 0.504129, 0.097906, 16.0)
_MU = (-0.148223, -0.290993, 0.439216, 128.0)
_MV = (0.439216, -0.367788, -0.071427, 128.0)


def ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def ease_out_back(t: float, s: float = 1.4) -> float:
    """Overshoots 1 on the way in, then settles back onto it."""
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


def to_yuv420(img: Image.Image) -> tuple[bytes, bytes, bytes]:
    """RGB image -> (Y, U, V) plane bytes, chroma 2x2 box-averaged. Matches ffmpeg's own
    conversion, so frames can be piped as yuv420p without a per-frame colour conversion.

    The width and height must be even (the half-size chroma planes drop an odd last row/column);
    `pipeline.prep` guarantees that for every image it hands to the effects."""
    w, h = img.size
    y = img.convert("L", _MY)
    u = img.convert("L", _MU).resize((w // 2, h // 2), Image.Resampling.BOX)
    v = img.convert("L", _MV).resize((w // 2, h // 2), Image.Resampling.BOX)
    return y.tobytes(), u.tobytes(), v.tobytes()
