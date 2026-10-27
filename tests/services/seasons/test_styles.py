from io import BytesIO

from PIL import Image

from nani_pix_bot.seasons import registry
from nani_pix_bot.services.seasons import styles


def _png(size=(64, 36), color=(200, 40, 40)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def test_a_tagged_game_gets_its_run_style(session, tagged_game) -> None:
    assert styles.style_for(session, tagged_game) == "plain"


def test_an_untagged_game_gets_none(session, untagged_game) -> None:
    assert styles.style_for(session, untagged_game) is None


def test_a_tagged_game_whose_run_is_unregistered_gets_none(
    session, tagged_game, active_season
) -> None:
    active_season.run_id = "gone_1"
    session.flush()
    assert styles.style_for(session, tagged_game) is None


def test_every_style_keeps_the_size() -> None:
    for key in styles.STYLES:
        out = Image.open(BytesIO(styles.apply_style(_png(), key)))
        assert out.size == (64, 36), key


def test_an_unknown_style_leaves_the_image_and_warns(log_records) -> None:
    png = _png()
    assert styles.apply_style(png, "nope") == png
    assert any(r.level == "WARNING" for r in log_records)


def test_a_style_that_raises_posts_the_stage_unstyled_and_logs(monkeypatch, log_records) -> None:
    def boom(image: Image.Image) -> Image.Image:
        raise ValueError("bad art")

    monkeypatch.setattr(styles, "STYLES", {"boom": boom})
    png = _png()
    assert styles.apply_style(png, "boom") == png
    assert any(r.level == "ERROR" and r.extra["style"] == "boom" for r in log_records)


def test_every_run_names_a_known_style() -> None:
    for runs in (registry.all_runs(), registry.discover("tests.seasons.fake_runs")):
        for run in runs.values():
            assert styles.problems(run) == [], run.run_id
