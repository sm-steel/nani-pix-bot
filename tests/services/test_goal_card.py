import io

import pytest
from PIL import Image

from nani_pix_bot.services.cards import backgrounds
from nani_pix_bot.services.cards.goal_card import (
    CANDLE_SCALE,
    CANDLE_SLOTS,
    GOAL_ART,
    GOAL_LAYERS,
    HORIZON_Y,
    GoalCard,
    lit_candles,
    pile_index,
    render_goal_card,
    rise,
)

RED, GREY, BG, BONE = (200, 20, 20), (120, 120, 120), (10, 40, 90), (240, 240, 200)
CLEAR = (0, 0, 0, 0)
MAGENTA = (255, 0, 255)


@pytest.mark.parametrize(
    ("s", "t", "pile", "lit"),
    [
        (0, 100, 0, 10),
        (1, 100, 0, 10),
        (10, 100, 0, 9),
        (50, 100, 2, 5),
        (99, 100, 4, 1),
        (100, 100, 4, 0),
        (130, 100, 4, 0),
    ],
)
def test_pile_and_candles(s, t, pile, lit):
    assert (pile_index(s, t), lit_candles(s, t)) == (pile, lit)


def test_rise_grows_with_progress():
    assert rise(0, 100) == (0.45, 0.5)
    assert rise(100, 100) == (0.56, 0.0)
    assert rise(200, 100) == (0.56, 0.0)


def _save(path, size, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, colour).save(path)


@pytest.fixture
def tmp_art(tmp_path, monkeypatch):
    folder = tmp_path / "demo_3" / GOAL_ART
    for name in GOAL_LAYERS:
        _save(folder / name, (1200, 630), CLEAR)
    _save(folder / "background.png", (1200, 630), (*BG, 255))
    _save(folder / "gashadokuro.png", (800, 826), (*BONE, 255))
    _save(folder / "candle_lit.png", (35, CANDLE_HEIGHT), (*RED, 255))
    _save(folder / "candle_out.png", (35, CANDLE_HEIGHT), (*GREY, 255))
    _save(folder / "boss.png", (600, 315), (90, 0, 90, 255))
    monkeypatch.setattr(backgrounds, "SEASON_ART_DIR", tmp_path)
    return folder


def _near(pixel, colour):
    return all(abs(a - b) <= 3 for a, b in zip(pixel[:3], colour, strict=True))


CANDLE_HEIGHT = 160


def _probe(slot: int, share: float = 0.5) -> tuple[int, int]:
    """A pixel `share` of the way down the slot's candle (0 = top, 1 = base)."""
    x, y = CANDLE_SLOTS[slot]
    return x, y - round(CANDLE_HEIGHT * CANDLE_SCALE * (1 - share))


def _redness(pixel) -> int:
    return pixel[0] - (pixel[1] + pixel[2]) // 2


def _brightness(pixel) -> int:
    return pixel[0] + pixel[1] + pixel[2]


def _draw(card: GoalCard) -> Image.Image:
    return Image.open(io.BytesIO(render_goal_card(card))).convert("RGB")


def test_render_draws_lit_and_snuffed_candles(tmp_art):
    img = _draw(GoalCard("demo_3", 50, 100))
    assert img.size == (1200, 630)
    # the lighting tints both toward the scene, but the lit one stays far redder
    assert _redness(img.getpixel(_probe(0))) > _redness(img.getpixel(_probe(9))) + 40


def test_a_candle_fades_darker_toward_its_base(tmp_art):
    img = _draw(GoalCard("demo_3", 50, 100))
    for slot in (0, 9):
        assert _brightness(img.getpixel(_probe(slot, 0.95))) < _brightness(
            img.getpixel(_probe(slot, 0.4))
        )


def test_the_skeleton_is_clipped_at_the_horizon(tmp_art):
    img = _draw(GoalCard("demo_3", 0, 100))
    assert _near(img.getpixel((600, HORIZON_Y - 5)), BONE)
    assert _near(img.getpixel((600, HORIZON_Y + 5)), BG)


def test_the_skeleton_grows_as_candles_go_out(tmp_art):
    start, end = _draw(GoalCard("demo_3", 0, 100)), _draw(GoalCard("demo_3", 100, 100))
    x = 600 - 360 // 2 - 5  # outside the smaller skeleton, inside the full-size one
    assert _near(start.getpixel((x, HORIZON_Y - 5)), BG)
    assert _near(end.getpixel((x, HORIZON_Y - 5)), BONE)


def test_the_skull_pile_follows_progress(tmp_art):
    _save(tmp_art / "skulls_2.png", (1200, 630), (*MAGENTA, 255))
    assert _near(_draw(GoalCard("demo_3", 50, 100)).getpixel((5, 620)), MAGENTA)
    assert not _near(_draw(GoalCard("demo_3", 10, 100)).getpixel((5, 620)), MAGENTA)


def test_the_counter_is_drawn_top_left(tmp_art):
    box = _draw(GoalCard("demo_3", 50, 100)).crop((0, 0, 300, 120))
    assert any(not _near(p, BG) for p in box.get_flattened_data())


def test_boss_card_is_the_boss_picture_alone(tmp_art):
    img = _draw(GoalCard("demo_3", 100, 100, boss=True))
    assert img.size == (1200, 630)
    assert _near(img.getpixel((600, 560)), (90, 0, 90))


@pytest.mark.parametrize("name", [*GOAL_LAYERS, "boss.png"])
def test_missing_layer_raises_file_not_found(tmp_art, name):
    (tmp_art / name).unlink()
    pile = int(name[7]) if name.startswith("skulls_") else 0  # only the shown pile is read
    with pytest.raises(FileNotFoundError):
        render_goal_card(GoalCard("demo_3", pile * 20, 100, boss=name == "boss.png"))
