"""The author's clue screenshot list (issue #450): what may join it, what
leaves it, and the key its entries are handed out under."""

from sqlalchemy.orm import Session

from nani_pix_bot.models import ClueAuthorScreenshot, ClueScreenshotHandout, Player
from nani_pix_bot.models.enums import Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import author_list
from nani_pix_bot.services.pixelate import fingerprint
from tests.frames import frame_bytes

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
