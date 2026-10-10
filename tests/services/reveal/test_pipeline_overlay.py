import pickle

import pytest

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
    assert pickle.loads(pickle.dumps(other)) == other  # noqa: S301


def test_the_default_overlay_uses_the_default_clip(monkeypatch, caplog) -> None:
    _renderers(monkeypatch)
    overlay = pipeline._overlay(_static(), pipeline.Badge(None, "@w"))
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
    assert pipeline._overlay(static, badge).confetti_mov == b"stars-clip"
    assert pipeline._overlay(static, badge).confetti_mov == b"stars-clip"
    assert pipeline._overlay(static, pipeline.Badge(None, "@w")).confetti_mov == b"default-clip"
    assert len(calls) == 1


def test_unknown_or_failing_overlay_falls_back_to_confetti(monkeypatch) -> None:
    def broken(_height: int) -> tuple[bytes, tuple[int, int]]:
        raise RuntimeError("renderer broke")

    _renderers(monkeypatch, broken=broken)
    for key in ("nope", "broken"):
        overlay = pipeline._overlay(_static(), pipeline.Badge(None, "@w", overlay=key))
        assert overlay.confetti_mov == b"default-clip"


def test_the_fallback_warns_via_stdlib_logging(monkeypatch, caplog) -> None:
    """The worker never imports loguru, so the warning goes through stdlib logging (stderr)."""

    def broken(_height: int) -> tuple[bytes, tuple[int, int]]:
        raise RuntimeError("renderer broke")

    _renderers(monkeypatch, broken=broken)
    with caplog.at_level("WARNING"):
        pipeline._overlay(_static(), pipeline.Badge(None, "@w", overlay="nope"))
        pipeline._overlay(_static(), pipeline.Badge(None, "@w", overlay="broken"))
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(messages) == 2
    assert "nope" in messages[0]
    assert "broken" in messages[1]


@pytest.mark.ffmpeg
def test_the_confetti_overlay_is_unchanged() -> None:
    from nani_pix_bot.services.reveal import celebration

    static = _static()
    clip = pipeline._overlay(static, pipeline.Badge(None, "@w")).confetti_mov
    assert clip == celebration.confetti_clip(celebration.badge_height(SIZE))[0]
