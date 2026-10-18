import pickle

import pytest

from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.reveal import overlays, pipeline

SIZE = (320, 180)


def _static() -> pipeline._Static:
    return pipeline._Static(SIZE, b"")


def _renderers(monkeypatch, **renderers) -> None:
    monkeypatch.setattr(overlays, "WIN_OVERLAYS", {overlays.DEFAULT_OVERLAY: _clip, **renderers})


def _clip(height: int) -> tuple[bytes, tuple[int, int]]:
    return b"default-clip", (height, height)


def test_badge_defaults_to_the_confetti_overlay_and_stays_picklable() -> None:
    badge = pipeline.Badge(avatar=None, handle="@w")
    assert badge.overlay == "confetti"
    other = pipeline.Badge(avatar=None, handle="@w", overlay="stars")
    assert pickle.dumps(other)
    assert all(type(v) in (str, bytes, type(None)) for v in vars(other).values())


def test_the_default_overlay_uses_the_default_clip(monkeypatch, caplog) -> None:
    _renderers(monkeypatch)
    with caplog.at_level("WARNING"):
        overlay = pipeline._overlay(_static(), pipeline.Badge(None, "@w"), Pose.WIN)
    assert overlay.confetti_mov == b"default-clip"
    assert caplog.records == []


def test_a_registered_overlay_is_used_and_cached_per_overlay(monkeypatch) -> None:
    calls: list[int] = []

    def stars(height: int) -> tuple[bytes, tuple[int, int]]:
        calls.append(height)
        return b"stars-clip", (height, height)

    _renderers(monkeypatch, stars=stars)
    static = _static()
    badge = pipeline.Badge(None, "@w", overlay="stars")
    assert pipeline._overlay(static, badge, Pose.WIN).confetti_mov == b"stars-clip"
    assert pipeline._overlay(static, badge, Pose.WIN).confetti_mov == b"stars-clip"
    assert (
        pipeline._overlay(static, pipeline.Badge(None, "@w"), Pose.WIN).confetti_mov
        == b"default-clip"
    )
    assert len(calls) == 1


def test_unknown_or_failing_overlay_falls_back_to_confetti(monkeypatch) -> None:
    def broken(_height: int) -> tuple[bytes, tuple[int, int]]:
        raise RuntimeError("renderer broke")

    _renderers(monkeypatch, broken=broken)
    for key in ("nope", "broken"):
        overlay = pipeline._overlay(_static(), pipeline.Badge(None, "@w", overlay=key), Pose.WIN)
        assert overlay.confetti_mov == b"default-clip"


def test_the_fallback_warns_via_stdlib_logging(monkeypatch, caplog) -> None:
    """The worker never imports loguru, so the warning goes through stdlib logging (stderr)."""

    def broken(_height: int) -> tuple[bytes, tuple[int, int]]:
        raise RuntimeError("renderer broke")

    _renderers(monkeypatch, broken=broken)
    with caplog.at_level("WARNING"):
        pipeline._overlay(_static(), pipeline.Badge(None, "@w", overlay="nope"), Pose.WIN)
        pipeline._overlay(_static(), pipeline.Badge(None, "@w", overlay="broken"), Pose.WIN)
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(messages) == 2
    assert "nope" in messages[0]
    assert "broken" in messages[1]


def test_drawn_ahead_overlays_are_not_drawn_again_at_the_win(monkeypatch) -> None:
    calls: list[str] = []

    def renderer(name: str):
        def render(height: int) -> tuple[bytes, tuple[int, int]]:
            calls.append(name)
            return f"{name}-clip".encode(), (height, height)

        return render

    _renderers(monkeypatch, stars=renderer("stars"), moons=renderer("moons"))
    static = _static()
    pipeline._draw_ahead(static, ("stars", "moons"))
    assert calls == ["stars", "moons"]
    for key in ("stars", "moons"):
        badge = pipeline.Badge(None, "@w", overlay=key)
        assert pipeline._overlay(static, badge, Pose.WIN).confetti_mov == f"{key}-clip".encode()
    assert calls == ["stars", "moons"]  # nothing drawn at the win


def test_a_failing_overlay_drawn_ahead_is_skipped_with_a_warning(monkeypatch, caplog) -> None:
    def broken(_height: int) -> tuple[bytes, tuple[int, int]]:
        raise RuntimeError("renderer broke")

    _renderers(monkeypatch, broken=broken)
    static = _static()
    with caplog.at_level("WARNING"):
        pipeline._draw_ahead(static, ("broken", "nope"))
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2
    assert "broken" in warnings[0]
    # the win still gets the default, as it would have without drawing ahead
    badge = pipeline.Badge(None, "@w", overlay="broken")
    assert pipeline._overlay(static, badge, Pose.WIN).confetti_mov == b"default-clip"


@pytest.mark.ffmpeg
def test_the_confetti_overlay_is_unchanged() -> None:
    from nani_pix_bot.services.reveal import celebration

    static = _static()
    clip = pipeline._overlay(static, pipeline.Badge(None, "@w"), Pose.WIN).confetti_mov
    assert clip == celebration.confetti_clip(celebration.badge_height(SIZE))[0]
