import dataclasses

import pytest
from PIL import Image

from nani_pix_bot.services.reveal import bone_rattle, overlay_canvas, overlays, petals

from .ffprobe_helpers import decode_clean, probe_stream

HEIGHT = 60
SIZE = overlay_canvas.clip_size(HEIGHT)


def _frame(raw: bytes, index: int) -> Image.Image:
    step = SIZE[0] * SIZE[1] * 4
    return Image.frombytes("RGBA", SIZE, raw[index * step : (index + 1) * step])


def _ivory(
    frame: Image.Image, box: tuple[int, int, int, int] | None = None
) -> list[tuple[int, int]]:
    """Every clearly visible bone-coloured pixel, as (x, y)."""
    area = frame if box is None else frame.crop(box)
    ox, oy = (0, 0) if box is None else box[:2]
    data, width = area.tobytes(), area.width
    return [
        (ox + (i // 4) % width, oy + (i // 4) // width)
        for i in range(0, len(data), 4)
        if data[i + 3] > 60 and min(data[i : i + 3]) > 200
    ]


def _bbox(points: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _peak_alpha(channel: Image.Image) -> int:
    histogram = channel.histogram()
    return max(level for level, count in enumerate(histogram) if count)


def _still(pose: bone_rattle._Pose) -> Image.Image:
    """One pose painted full strength on its own layer, at the render's scale."""
    unit = bone_rattle.SKELETON_HEIGHT * HEIGHT * bone_rattle.SS
    layer = Image.new("RGBA", (round(1.3 * unit), round(1.15 * unit)), (0, 0, 0, 0))
    bone_rattle._paint_skeleton(
        bone_rattle._Skeleton(layer, unit, layer.width / 2, bone_rattle.LAYER_GROUND * unit), pose
    )
    return layer


@pytest.fixture(scope="module")
def rattle() -> bytes:
    return bone_rattle.rattle_frames(HEIGHT)


def test_rattle_size_and_frame_count(rattle) -> None:
    assert len(rattle) == bone_rattle.RATTLE_FRAMES * SIZE[0] * SIZE[1] * 4


def test_rattle_shows_ivory_bones_mid_ending(rattle) -> None:
    assert len(_ivory(_frame(rattle, bone_rattle.RATTLE_FRAMES // 2))) > 300


def test_the_skeleton_stands_on_the_badge_edge_at_its_spot(rattle) -> None:
    x0, y0, x1, y1 = _bbox(_ivory(_frame(rattle, bone_rattle.RATTLE_FRAMES // 2)))
    assert y1 < SIZE[1] - HEIGHT + 0.1 * HEIGHT  # feet at the badge top, not inside it
    assert y0 < SIZE[1] - HEIGHT - 1.5 * HEIGHT  # and it is tall
    assert x0 < bone_rattle.STAND_X * HEIGHT < x1


def test_the_skeleton_dances(rattle) -> None:
    a, b = _frame(rattle, 8), _frame(rattle, 16)
    assert a.tobytes() != b.tobytes()
    assert _bbox(_ivory(a)) != _bbox(_ivory(b))


def test_the_limbs_follow_the_seeded_dance_cycle() -> None:
    first, again = bone_rattle._pose(3), bone_rattle._pose(3 + bone_rattle.DANCE_PERIOD)
    assert [a for pair in first.arms + first.legs for a in pair] == pytest.approx(
        [a for pair in again.arms + again.legs for a in pair]
    )
    assert bone_rattle._pose(3).arms != bone_rattle._pose(7).arms


def test_the_dancing_poses_are_painted(rattle, monkeypatch) -> None:
    still = bone_rattle._pose(0)
    monkeypatch.setattr(bone_rattle, "_pose", lambda _f: still)
    assert bone_rattle.rattle_frames(HEIGHT) != rattle


def test_the_jaw_opens_and_shuts_on_a_six_frame_period() -> None:
    jaws = [bone_rattle._pose(f).jaw for f in range(bone_rattle.CLACK_PERIOD)]
    assert jaws[0] == pytest.approx(0)
    assert max(jaws) == pytest.approx(bone_rattle.JAW_OPEN)
    assert bone_rattle._pose(6).jaw == pytest.approx(bone_rattle._pose(0).jaw)
    assert bone_rattle._pose(3).jaw > bone_rattle._pose(1).jaw


def test_the_jaw_clacks_open_beneath_the_skull() -> None:
    shut = bone_rattle._pose(0)
    wide = dataclasses.replace(shut, jaw=bone_rattle.JAW_OPEN)
    shut_img, wide_img = _still(shut), _still(wide)
    unit = bone_rattle.SKELETON_HEIGHT * HEIGHT * bone_rattle.SS
    neck = bone_rattle.RIBS_PIVOT[1] + bone_rattle.NECK + bone_rattle.SKULL_LIFT
    skull_bottom = (bone_rattle.LAYER_GROUND - neck) * unit  # layer y, at zero sway
    # the band the open jaw drops into: right under the shut jaw, by the full drop
    top = round(skull_bottom + 0.06 * unit)
    band = (0, top, shut_img.width, top + round(0.05 * unit))
    assert _ivory(shut_img, band) != _ivory(wide_img, band)
    # the skull hides the top of the jaw, so above its teeth nothing changes
    above = (0, 0, shut_img.width, round(skull_bottom - 0.05 * unit))
    assert _ivory(shut_img, above) == _ivory(wide_img, above)


def test_the_limb_angles_reach_the_pixels() -> None:
    pose = bone_rattle._pose(3)
    swung = dataclasses.replace(
        pose,
        arms=tuple((a + 40, b + 40) for a, b in pose.arms),
        legs=tuple((a + 15, b + 15) for a, b in pose.legs),
    )
    assert _still(pose).tobytes() != _still(swung).tobytes()
    arms_only = dataclasses.replace(pose, arms=swung.arms)
    legs_only = dataclasses.replace(pose, legs=swung.legs)
    assert _still(pose).tobytes() != _still(arms_only).tobytes()
    assert _still(pose).tobytes() != _still(legs_only).tobytes()


def test_the_rendered_frames_use_the_jaw(rattle, monkeypatch) -> None:
    monkeypatch.setattr(bone_rattle, "_jaw_open", lambda _f: 0.0)
    assert bone_rattle.rattle_frames(HEIGHT) != rattle


def test_the_parts_shake_by_a_pixel_between_frames() -> None:
    shakes = {s for f in range(20) for s in bone_rattle._pose(f).shake}
    assert len(shakes) > 1
    limit = bone_rattle.JITTER * bone_rattle.SS
    assert all(abs(v) <= limit for s in shakes for v in s)


def test_a_missing_part_is_skipped_not_fatal(monkeypatch) -> None:
    def gone(*_args: object) -> None:
        return None

    monkeypatch.setattr(bone_rattle, "sprite_or_none", gone)
    bone_rattle._rotated.cache_clear()
    try:
        assert not _still(bone_rattle._pose(0)).getbbox()
    finally:
        bone_rattle._rotated.cache_clear()


def test_the_rotation_cache_is_bounded() -> None:
    assert bone_rattle._rotated.cache_info().maxsize is not None


def test_rattle_fades_in_and_out(rattle) -> None:
    n = bone_rattle.RATTLE_FRAMES
    first = _peak_alpha(_frame(rattle, 0).getchannel("A"))
    middle = _peak_alpha(_frame(rattle, n // 2).getchannel("A"))
    last = _peak_alpha(_frame(rattle, n - 1).getchannel("A"))
    assert first < middle
    assert last < middle // 4


def test_rattle_is_deterministic(rattle) -> None:
    assert bone_rattle.rattle_frames(HEIGHT) == rattle


def test_rattle_is_no_longer_than_the_sakura_storm() -> None:
    assert bone_rattle.RATTLE_FRAMES <= petals.STORM_FRAMES


def test_bone_rattle_is_a_registered_win_overlay() -> None:
    assert overlays.WIN_OVERLAYS["bone_rattle"] is bone_rattle.bone_rattle_clip


@pytest.mark.ffmpeg
def test_bone_rattle_clip_decodes() -> None:
    clip, size = bone_rattle.bone_rattle_clip(HEIGHT)
    assert decode_clean(clip)
    assert probe_stream(clip) == ("qtrle", *size, bone_rattle.RATTLE_FRAMES)
