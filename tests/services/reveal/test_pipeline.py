import io
import pickle

import pytest
from PIL import Image

from nani_pix_bot.models.enums import PixelAlgorithm, RevealEffect
from nani_pix_bot.services.reveal import celebration, pipeline
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames

WIDTHS = (64, 80, 128, 192, 512)


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
    assert decode_clean(pipeline.finish(pregen, png, None))


@pytest.mark.ffmpeg
def test_finish_with_badge_adds_the_ending() -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    plain = pipeline.finish(pregen, png, None)
    badged = pipeline.finish(pregen, png, pipeline.Badge(avatar=None, handle="@winner"))
    assert decode_clean(badged)
    assert probe_frames(badged) == probe_frames(plain)
    assert badged != plain


@pytest.mark.ffmpeg
def test_finish_works_from_a_cold_or_replaced_cache() -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    pipeline._static = None
    assert decode_clean(pipeline.finish(pregen, png, pipeline.Badge(None, "@w")))
    pipeline.pregenerate(_png((160, 90)), PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    assert decode_clean(pipeline.finish(pregen, png, None))  # other image cached: rebuilt


@pytest.mark.ffmpeg
def test_pregenerate_builds_the_confetti_so_finish_does_not(monkeypatch) -> None:
    png = _png()
    pregen = pipeline.pregenerate(png, PixelAlgorithm.MEDIAN, WIDTHS, RevealEffect.IRIS)
    assert pipeline._static is not None
    static = pipeline._static[1]
    assert celebration.badge_height(static.size) in static.confetti

    def boom(_height):
        raise AssertionError("confetti rebuilt at win time")

    monkeypatch.setattr(celebration, "confetti_clip", boom)
    assert decode_clean(pipeline.finish(pregen, png, pipeline.Badge(None, "@w")))
