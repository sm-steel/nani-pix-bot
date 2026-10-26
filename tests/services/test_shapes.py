from nani_pix_bot.services.shapes import bone_outline


def test_bone_outline_spans_both_ends_and_is_wider_at_the_knobs() -> None:
    thickness = 10.0
    points = bone_outline((0.0, 0.0), (100.0, 0.0), thickness)
    xs = [x for x, _ in points]
    assert min(xs) < 0  # the knobs reach past both ends
    assert max(xs) > 100
    ys = [y for _, y in points]
    assert max(ys) - min(ys) > thickness  # the knobs stand out of the bar
    bar = {round(abs(y), 6) for x, y in points if round(x, 6) in (7.5, 92.5)}
    assert bar == {thickness / 2}  # between the knobs the bone is exactly `thickness` wide


def test_bone_outline_follows_the_direction() -> None:
    points = bone_outline((0.0, 0.0), (0.0, 100.0), 10.0)
    assert min(y for _, y in points) < 0
    assert max(y for _, y in points) > 100


def test_a_zero_length_bone_is_empty() -> None:
    assert bone_outline((5.0, 5.0), (5.0, 5.0), 10.0) == []
