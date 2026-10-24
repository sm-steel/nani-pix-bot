import io
import pickle
from pathlib import Path

import pytest
from PIL import Image

from nani_pix_bot.models.enums import PixelAlgorithm, RevealEffect
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.reveal import celebration, overlays, pipeline
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames

WIDTHS = (64, 80, 128, 192, 512)


def _end(badge: pipeline.Badge | None, pose: Pose, costume: str | None = None) -> pipeline.Ending:
    return pipeline.Ending(badge, pipeline.Nani(pose, costume))


def _png(size=(320, 180)) -> bytes:
    image = Image.new("RGB", size, (90, 140, 200))
    image.paste((230, 190, 160), (150, 40, 220, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prep_caps_width_and_makes_even_dimensions() -> None:
    original, stages = pipeline.prep(_png((1921, 1081)), PixelAlgorithm.BOX, WIDTHS)
    assert original.size == (1280, 720)
    assert len(stages) == 5
    assert all(s.size == original.size for s in stages)


def test_pregen_is_picklable_for_the_process_pool() -> None:
    pregen = pipeline.Pregen(part1_ts=b"\x47", join_offset=1.0, render_ms=5)
    badge = pipeline.Badge(avatar=None, handle="@w")
    for obj in (pregen, badge):  # round-trips our own objects, nothing untrusted
        assert pickle.loads(pickle.dumps(obj)) == obj  # noqa: S301


def test_warm_up_is_a_noop() -> None:
    assert pipeline.warm_up() is None


@pytest.mark.ffmpeg
@pytest.mark.parametrize("effect", list(RevealEffect))
def test_pregenerate_then_finish_without_badge(effect) -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, effect)
    assert pregen.part1_ts[:1] == b"\x47"  # MPEG-TS sync byte
    assert decode_clean(pipeline.finish(pregen, png, _end(None, Pose.OOPS)))


@pytest.mark.ffmpeg
def test_finish_with_badge_adds_the_ending() -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    plain = pipeline.finish(pregen, png, _end(None, Pose.OOPS))
    badged = pipeline.finish(
        pregen, png, _end(pipeline.Badge(avatar=None, handle="@winner"), Pose.WIN)
    )
    assert decode_clean(badged)
    assert probe_frames(badged) == probe_frames(plain)
    assert badged != plain


@pytest.mark.ffmpeg
def test_finish_works_from_a_cold_or_replaced_cache() -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    pipeline._static = None
    assert decode_clean(pipeline.finish(pregen, png, _end(pipeline.Badge(None, "@w"), Pose.WIN)))
    pipeline.pregenerate(_png((160, 90)), PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    # other image cached: rebuilt
    assert decode_clean(pipeline.finish(pregen, png, _end(None, Pose.OOPS)))


@pytest.mark.ffmpeg
def test_pregenerate_builds_the_confetti_so_finish_does_not(monkeypatch) -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    assert pipeline._static is not None
    static = pipeline._static[1]
    assert ("confetti", celebration.badge_height(static.size)) in static.confetti

    def boom(_height):
        raise AssertionError("confetti rebuilt at win time")

    monkeypatch.setattr(overlays, "WIN_OVERLAYS", {"confetti": boom})
    assert decode_clean(pipeline.finish(pregen, png, _end(pipeline.Badge(None, "@w"), Pose.WIN)))


@pytest.mark.ffmpeg
def test_pregenerate_draws_the_equipped_overlays_so_the_win_does_not(monkeypatch) -> None:
    png = _png()
    pregen = pipeline.pregenerate(
        png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS, pipeline.Looks(("petal_drift",))
    )
    static = pipeline._static[1] if pipeline._static else None
    assert static is not None
    assert ("petal_drift", celebration.badge_height(static.size)) in static.confetti

    def boom(_height):
        raise AssertionError("overlay drawn at win time")

    monkeypatch.setattr(overlays, "WIN_OVERLAYS", {"confetti": boom, "petal_drift": boom})
    badge = pipeline.Badge(None, "@w", overlay="petal_drift")
    assert decode_clean(pipeline.finish(pregen, png, _end(badge, Pose.WIN)))


@pytest.mark.ffmpeg
def test_preview_clip_renders_a_whole_reveal_and_leaves_the_game_cache_alone() -> None:
    png = _png()
    pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    cached = pipeline._static
    clip = pipeline.preview_clip(
        _png((160, 90)),
        PixelAlgorithm.MEDIAN,
        WIDTHS,
        RevealEffect.IRIS,
        _end(pipeline.Badge(None, "@you"), Pose.WIN),
    )
    assert decode_clean(clip)
    assert pipeline._static is cached


def test_nothing_run_by_the_worker_imports_loguru() -> None:
    """The spawned worker never runs setup_logging, so its log lines would carry no game context
    (and break LOG_FORMAT=json); timings are logged bot-side. store.py runs bot-side only."""
    package = Path(pipeline.__file__).parent
    offenders = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if path.name != "store.py" and "loguru" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
