import random

from nani_pix_bot.models.enums import RevealEffect, RevealStatus
from nani_pix_bot.models.reveal_video import RevealVideo
from nani_pix_bot.services.reveal import store


def test_reserve_creates_the_single_pending_slot(session) -> None:
    store.reserve(session, 7, RevealEffect.IRIS, None)
    row = store.load(session)
    assert row is not None
    assert (row.slot, row.game_id, row.effect) == (1, 7, RevealEffect.IRIS)
    assert row.status is RevealStatus.PENDING
    assert row.part1_ts is None
    assert row.image_choice is None


def test_reserve_replaces_the_previous_game(session) -> None:
    store.reserve(session, 7, RevealEffect.IRIS, None)
    assert store.mark_ready(session, 7, b"ts", 1.5)
    store.reserve(session, 8, RevealEffect.GLITCH, "b")
    rows = session.query(RevealVideo).all()
    assert len(rows) == 1
    assert (rows[0].game_id, rows[0].effect, rows[0].image_choice) == (8, RevealEffect.GLITCH, "b")
    assert rows[0].status is RevealStatus.PENDING
    assert rows[0].part1_ts is None


def test_mark_ready_fills_bytes_and_time(session) -> None:
    store.reserve(session, 7, RevealEffect.RIPPLE, None)
    assert store.mark_ready(session, 7, b"ts-bytes", 2.25)
    row = store.load(session)
    assert row is not None
    assert (row.status, row.part1_ts, row.join_offset) == (RevealStatus.READY, b"ts-bytes", 2.25)
    assert row.ready_at is not None


def test_stale_game_cannot_touch_a_newer_slot(session) -> None:
    store.reserve(session, 7, RevealEffect.IRIS, None)
    store.reserve(session, 8, RevealEffect.SHATTER, None)
    assert not store.mark_ready(session, 7, b"old", 1.0)
    assert not store.mark_failed(session, 7)
    assert not store.clear(session, 7)
    row = store.load(session)
    assert row is not None
    assert row.game_id == 8
    assert row.status is RevealStatus.PENDING


def test_mark_failed_and_clear(session) -> None:
    store.reserve(session, 7, RevealEffect.TILE_FLIP, None)
    assert store.mark_failed(session, 7)
    row = store.load(session)
    assert row is not None
    assert row.status is RevealStatus.FAILED
    assert store.clear(session, 7)
    assert store.load(session) is None


def test_load_on_empty_table_is_none(session) -> None:
    assert store.load(session) is None


def test_picks_cover_every_effect_and_both_images() -> None:
    rng = random.Random(1)  # noqa: S311 - deterministic test rng, not crypto
    assert {store.pick_effect(rng) for _ in range(200)} == set(RevealEffect)
    assert {store.pick_image(rng) for _ in range(50)} == {"a", "b"}
