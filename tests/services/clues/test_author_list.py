"""The author's clue screenshot list (issue #450): what may join it, what
leaves it, and the key its entries are handed out under."""

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import ClueAuthorScreenshot, ClueScreenshotHandout, Player
from nani_pix_bot.models.enums import Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import author_list
from nani_pix_bot.services.pixelate import fingerprint
from tests.frames import encode, frame, frame_bytes

STARTER = 1


def _game(session: Session, *, main_seed: int | None = 0) -> Game:
    session.add(Player(telegram_user_id=STARTER))
    session.flush()
    main = frame_bytes(main_seed) if main_seed is not None else None
    game = Game(starter_id=STARTER, original_image=main, shikimori_id=52991)
    session.add(game)
    session.flush()
    return game


def _entry(session: Session, game: Game, seed: int, url: str | None = None) -> ClueAuthorScreenshot:
    image = frame_bytes(seed)
    row = ClueAuthorScreenshot(
        game_id=game.id,
        source=Provider.SHIKIMORI if url else None,
        url=url,
        image=image,
        fingerprint=fingerprint.fingerprint(image),
    )
    session.add(row)
    session.flush()
    return row


def test_entries_in_id_order(session: Session) -> None:
    game = _game(session)
    first, second = _entry(session, game, 1), _entry(session, game, 2)

    assert [row.id for row in author_list.entries(session, game.id)] == [first.id, second.id]


def test_entries_only_for_the_game(session: Session) -> None:
    game = _game(session)
    other = Game(starter_id=STARTER)
    session.add(other)
    session.flush()
    _entry(session, other, 1)

    assert author_list.entries(session, game.id) == []


def test_handout_key_url_or_author_id(session: Session) -> None:
    game = _game(session)
    picked = _entry(session, game, 1, url="https://x/1.jpg")
    uploaded = _entry(session, game, 2)

    assert author_list.handout_key(picked) == "https://x/1.jpg"
    assert author_list.handout_key(uploaded) == f"author:{uploaded.id}"


def test_unsold_skips_handed_out_and_shown(session: Session) -> None:
    game = _game(session)
    game.shown_screenshot_urls = ["https://x/main.jpg"]
    _entry(session, game, 1, url="https://x/main.jpg")
    sold = _entry(session, game, 2)
    left = _entry(session, game, 3, url="https://x/3.jpg")
    session.add(
        ClueScreenshotHandout(game_id=game.id, screenshot_url=author_list.handout_key(sold))
    )
    session.flush()

    assert [row.id for row in author_list.unsold(session, game)] == [left.id]


def test_clear_images(session: Session) -> None:
    game = _game(session)
    _entry(session, game, 1)
    _entry(session, game, 2, url="https://x/2.jpg")

    assert author_list.clear_images(session, game.id) == 2

    rows = author_list.entries(session, game.id)
    assert len(rows) == 2
    session.expire_all()
    assert [row.image for row in author_list.entries(session, game.id)] == [None, None]


def _add(session: Session, game: Game, seed: int, url: str | None = None) -> author_list.AddResult:
    source = Provider.SHIKIMORI if url else None
    return author_list.add(session, game, author_list.NewEntry(frame_bytes(seed), source, url))


def test_add_upload(session: Session, log_records) -> None:
    game = _game(session)

    assert _add(session, game, 1) is author_list.AddResult.ADDED

    [row] = author_list.entries(session, game.id)
    assert (row.source, row.url, row.image) == (None, None, frame_bytes(1))
    assert row.fingerprint == fingerprint.fingerprint(frame_bytes(1))
    assert [line.level for line in log_records if "author's list" in line.message] == ["INFO"]


def test_add_from_a_source(session: Session) -> None:
    game = _game(session)

    assert _add(session, game, 1, url="https://x/1.jpg") is author_list.AddResult.ADDED

    [row] = author_list.entries(session, game.id)
    assert (row.source, row.url) == (Provider.SHIKIMORI, "https://x/1.jpg")


def test_add_refuses_duplicate_of_main_image(session: Session, log_records) -> None:
    game = _game(session, main_seed=0)
    # The same frame, re-encoded: the fingerprint matches, the bytes don't.
    same = encode(frame(0), "JPEG", quality=70)

    result = author_list.add(session, game, author_list.NewEntry(same))

    assert result is author_list.AddResult.DUPLICATE
    assert author_list.entries(session, game.id) == []
    assert any(line.level == "WARNING" for line in log_records)


def test_add_refuses_duplicate_entry(session: Session) -> None:
    game = _game(session)
    _add(session, game, 1)

    assert _add(session, game, 1, url="https://x/other.jpg") is author_list.AddResult.DUPLICATE
    assert len(author_list.entries(session, game.id)) == 1


def test_add_refuses_same_url(session: Session) -> None:
    game = _game(session)
    _add(session, game, 1, url="https://x/1.jpg")

    assert _add(session, game, 2, url="https://x/1.jpg") is author_list.AddResult.DUPLICATE
    assert len(author_list.entries(session, game.id)) == 1


def test_eleventh_add_is_full(session: Session) -> None:
    game = _game(session)
    limit = author_list.MAX_AUTHOR_SCREENSHOTS
    results = [_add(session, game, seed) for seed in range(1, limit + 2)]

    assert results[:-1] == [author_list.AddResult.ADDED] * limit
    assert results[-1] is author_list.AddResult.FULL
    assert len(author_list.entries(session, game.id)) == 10


def test_add_unreadable_raises(session: Session) -> None:
    game = _game(session)

    with pytest.raises(ValueError, match="not a readable image"):
        author_list.add(session, game, author_list.NewEntry(b"not an image"))
    assert author_list.entries(session, game.id) == []


def test_remove(session: Session, log_records) -> None:
    game = _game(session)
    keep, gone = _entry(session, game, 1), _entry(session, game, 2)

    assert author_list.remove(session, game.id, gone.id) is True

    assert [row.id for row in author_list.entries(session, game.id)] == [keep.id]
    assert any(line.level == "INFO" and "removed" in line.message for line in log_records)


def test_remove_unknown_id_false(session: Session) -> None:
    game = _game(session)
    other = Game(starter_id=STARTER)
    session.add(other)
    session.flush()
    foreign = _entry(session, other, 1)

    assert author_list.remove(session, game.id, 999) is False
    assert author_list.remove(session, game.id, foreign.id) is False
    assert len(author_list.entries(session, other.id)) == 1
