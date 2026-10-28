"""Nani Pix in the corner of the game's images (issue #461)."""

import io
from pathlib import Path

import pytest
from PIL import Image

from nani_pix_bot.services import mascot
from nani_pix_bot.services.mascot import Pose

SAMPLE = Path(mascot.__file__).resolve().parent.parent / "assets" / "sample_screenshot.jpg"


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("number", "total", "overthrow", "pose"),
    [
        (1, 5, False, Pose.CAST),
        (1, 5, True, Pose.SLY),
        (2, 5, False, Pose.NOPE),
        (4, 5, False, Pose.NOPE),
        (5, 5, False, Pose.NANI),
        (5, 5, True, Pose.NANI),
        (1, 2, True, Pose.SLY),
        (2, 2, False, Pose.NANI),
    ],
)
def test_stage_pose(number: int, total: int, overthrow: bool, pose: Pose) -> None:
    assert mascot.stage_pose(number, total, overthrow=overthrow) is pose


def test_box_is_16_percent_wide_bottom_right_with_2_percent_margin() -> None:
    x, y, w, h = mascot.box((1000, 600), Pose.CAST)
    assert w == 160
    assert x == 1000 - 160 - 20
    assert y == 600 - h - 20


def test_box_keeps_the_pose_aspect() -> None:
    _, _, w, h = mascot.box((1000, 600), Pose.CAST)
    source = mascot.sized(Pose.CAST, mascot.SOURCE_WIDTH)
    assert h == round(source.height * w / source.width)


def test_box_never_upscales_past_the_source() -> None:
    _, _, w, _ = mascot.box((4000, 2250), Pose.CAST)
    assert w == mascot.SOURCE_WIDTH


def test_box_is_empty_on_a_tiny_image() -> None:
    assert mascot.box((40, 30), Pose.CAST)[2] == 0


def test_box_fits_a_tall_portrait() -> None:
    x, y, w, h = mascot.box((300, 1200), Pose.CAST)
    assert x >= 0
    assert y >= 0
    assert x + w <= 300
    assert y + h <= 1200


def test_box_fits_a_short_panorama() -> None:
    x, y, w, h = mascot.box((3000, 200), Pose.CAST)
    assert w > 0
    assert y >= 0
    assert y + h <= 200
    assert x + w <= 3000


def test_nani_is_sharp_on_top_of_the_picture() -> None:
    base = Image.new("RGB", (640, 360), (40, 120, 200))
    out = mascot.compose(base, Pose.NOPE)
    x, y, w, h = mascot.box(base.size, Pose.NOPE)
    expected = Image.new("RGBA", (w, h), (40, 120, 200, 255))
    expected.alpha_composite(mascot.sized(Pose.NOPE, w))
    assert out.crop((x, y, x + w, y + h)).tobytes() == expected.convert("RGB").tobytes()
    # nothing outside her box changes
    assert out.crop((0, 0, x, 360)).getcolors() == [(x * 360, (40, 120, 200))]


def test_compose_returns_a_copy() -> None:
    base = Image.new("RGB", (640, 360), (0, 0, 0))
    mascot.compose(base, Pose.CAST)
    assert base.getcolors() == [(640 * 360, (0, 0, 0))]


def test_compose_on_a_tiny_image_changes_nothing() -> None:
    base = Image.new("RGB", (40, 30), (9, 9, 9))
    assert mascot.compose(base, Pose.CAST).tobytes() == base.tobytes()


def test_compose_failure_returns_the_image_and_logs_error(monkeypatch, log_records) -> None:
    def broken(pose: Pose, width: int, costume: str | None = None) -> Image.Image:
        raise OSError("unreadable pose")

    monkeypatch.setattr(mascot, "sized", broken)
    base = Image.new("RGB", (640, 360), (1, 2, 3))
    assert mascot.compose(base, Pose.CAST).tobytes() == base.tobytes()
    errors = [r for r in log_records if r.level == "ERROR"]
    assert len(errors) == 1
    assert errors[0].extra["pose"] == "cast"


def test_compose_logs_the_pose_at_debug(log_records) -> None:
    mascot.compose(Image.new("RGB", (640, 360)), Pose.WIN)
    debug = [r for r in log_records if r.level == "DEBUG" and r.extra.get("pose") == "win"]
    assert len(debug) == 1
    assert debug[0].extra["width"] == mascot.box((640, 360), Pose.WIN)[2]


@pytest.mark.parametrize("add", [mascot.add_to_png, mascot.add_to_jpeg])
def test_unreadable_bytes_go_out_unchanged_and_log_error(add, log_records) -> None:
    assert add(b"not an image", Pose.CAST) == b"not an image"
    errors = [r for r in log_records if r.level == "ERROR"]
    assert len(errors) == 1
    assert errors[0].extra["pose"] == "cast"


def test_add_to_png_returns_a_png_of_the_same_size() -> None:
    out = Image.open(io.BytesIO(mascot.add_to_png(_png(Image.new("RGB", (800, 450))), Pose.CAST)))
    assert out.format == "PNG"
    assert out.size == (800, 450)


@pytest.mark.parametrize("pose", list(Pose))
def test_every_pose_asset_is_384_wide(pose: Pose) -> None:
    assert mascot.sized(pose, mascot.SOURCE_WIDTH).width == 384


def test_ending_pose() -> None:
    assert mascot.ending_pose(won=True) is Pose.WIN
    assert mascot.ending_pose(won=False) is Pose.OOPS
    assert mascot.ending_pose(won=False, vote_open=True) is Pose.NANI


@pytest.mark.parametrize("mode", ["RGBA", "P", "CMYK", "L"])
def test_add_to_jpeg_accepts_any_mode(mode: str) -> None:
    source = Image.new(mode, (640, 360))
    buf = io.BytesIO()
    source.save(buf, "TIFF" if mode == "CMYK" else "PNG")
    out = Image.open(io.BytesIO(mascot.add_to_jpeg(buf.getvalue(), Pose.OOPS)))
    assert out.format == "JPEG"
    assert out.mode == "RGB"
    assert out.size == (640, 360)


def test_add_to_jpeg_keeps_a_large_photo_under_telegrams_limit() -> None:
    big = Image.open(SAMPLE).convert("RGB").resize((4000, 2250), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    big.save(buf, "JPEG", quality=95)
    assert len(mascot.add_to_jpeg(buf.getvalue(), Pose.WIN)) < 10 * 1024 * 1024


def test_still_png_is_the_sized_pose_and_its_margin() -> None:
    png, margin = mascot.still_png(Pose.CAST, (1280, 720))
    still = Image.open(io.BytesIO(png))
    _, _, w, h = mascot.box((1280, 720), Pose.CAST)
    assert still.mode == "RGBA"
    assert still.size == (w, h)
    assert margin == round(1280 * mascot.MARGIN)


def _near_orange(pixel: bytes) -> bool:
    return all(abs(a - b) <= 3 for a, b in zip(pixel, (255, 120, 0), strict=True))


def test_pose_path_of_a_costume() -> None:
    assert mascot.pose_path(Pose.WIN, "kitsune") == mascot.COSTUME_DIR / "kitsune" / "win.png"


def test_pose_path_plain_is_unchanged() -> None:
    assert mascot.pose_path(Pose.WIN) == mascot.POSE_DIR / "win.png"


def test_compose_draws_the_costume(costume_art: str) -> None:
    base = Image.new("RGB", (640, 360), (0, 0, 0))
    out = mascot.compose(base, Pose.CAST, costume_art)
    x, y, w, h = mascot.box(base.size, Pose.CAST, costume_art)
    cx, cy = x + w // 2, y + h // 2
    assert _near_orange(out.crop((cx, cy, cx + 1, cy + 1)).tobytes())


def test_box_uses_the_costumes_own_aspect(costume_art: str) -> None:
    margin = round(1000 * mascot.MARGIN)
    room = 150 - 2 * margin
    _, _, w, h = mascot.box((1000, 150), Pose.CAST, costume_art)
    assert h == room
    assert w == room * 384 // 440


def test_a_missing_costume_pose_falls_back_to_plain_and_logs_error_once(
    costume_art: str, log_records
) -> None:
    (mascot.COSTUME_DIR / costume_art / "nope.png").unlink()
    base = Image.new("RGB", (640, 360), (5, 6, 7))
    first = mascot.compose(base, Pose.NOPE, costume_art)
    second = mascot.compose(base, Pose.NOPE, costume_art)
    assert first.tobytes() == second.tobytes() == mascot.compose(base, Pose.NOPE).tobytes()
    errors = [r for r in log_records if r.level == "ERROR"]
    assert len(errors) == 1
    assert errors[0].extra["costume"] == costume_art
    assert errors[0].extra["pose"] == "nope"


def test_still_png_sizes_the_costume(costume_art: str) -> None:
    png, _ = mascot.still_png(Pose.CAST, (1280, 720), costume_art)
    still = Image.open(io.BytesIO(png))
    assert still.height == round(440 * still.width / 384)


def test_add_to_jpeg_with_a_costume_keeps_size(costume_art: str) -> None:
    out = Image.open(
        io.BytesIO(mascot.add_to_jpeg(_png(Image.new("RGB", (800, 450))), Pose.WIN, costume_art))
    )
    assert out.size == (800, 450)


@pytest.mark.parametrize(
    "path",
    sorted(mascot.COSTUME_DIR.glob("*/*.png")) if mascot.COSTUME_DIR.exists() else [],
)
def test_every_costume_pose_asset_is_384_wide_and_short_enough(path: Path) -> None:
    with Image.open(path) as img:
        assert img.width == 384
        assert img.height <= 480
        assert img.mode == "RGBA"


def test_every_costume_dir_has_all_six_poses() -> None:
    if not mascot.COSTUME_DIR.exists():
        return
    for folder in mascot.COSTUME_DIR.iterdir():
        if folder.is_dir():
            assert {p.stem for p in folder.glob("*.png")} == {p.value for p in Pose}, folder
