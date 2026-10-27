import hashlib

from PIL import ImageDraw

from nani_pix_bot.services.reveal import overlay_canvas, petals

# Captured from petals.py before the canvas moved out of it.
DRIFT_SHA = "bde9878b169db551c2bacc6906f88fab1a66b52cc8251823c8e8d7cad654d044"
STORM_SHA = "b13f96b6c11a7190675cf93cb501d51e8acef5388090b8a07974306e6d869b64"


def test_petal_clips_are_byte_identical_after_the_move() -> None:
    assert hashlib.sha256(petals.drift_frames(60)).hexdigest() == DRIFT_SHA
    assert hashlib.sha256(petals.storm_frames(60)).hexdigest() == STORM_SHA


def test_clip_size_matches_the_confetti_area() -> None:
    assert overlay_canvas.clip_size(60) == (450, 216)


def test_alpha_fades_in_and_out() -> None:
    assert overlay_canvas.alpha(0, 56) < overlay_canvas.alpha(10, 56) == 255
    assert overlay_canvas.alpha(55, 56) < 255


def test_render_returns_raw_rgba_per_frame() -> None:
    def draw_frame(draw: ImageDraw.ImageDraw, f: int, alpha: int) -> None:
        draw.rectangle((0, 0, 99, 99), fill=(255, 0, 0, alpha))

    raw = overlay_canvas.render((40, 30), 10, 3, draw_frame)
    assert len(raw) == 40 * 30 * 4 * 3
