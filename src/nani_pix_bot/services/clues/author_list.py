"""The author's clue screenshot list (issue #450): the screenshots a game's
starter picked from a Shikimori/TMDB gallery or uploaded on the setup
preview's clue panel, which the shop's "Extra screenshot" clue sells from
according to `Game.clue_screenshot_mode`. Only changeable during setup;
frozen once the game is live. See MECHANICS.md's "Clue shop"."""

import enum
from dataclasses import dataclass
from typing import cast

from loguru import logger
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.orm import Session, object_session

from nani_pix_bot.models.clue_author_screenshot import ClueAuthorScreenshot
from nani_pix_bot.models.enums import ClueScreenshotMode, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import handouts
from nani_pix_bot.services.pixelate import fingerprint

MAX_AUTHOR_SCREENSHOTS = 10
# The prefix of an uploaded entry's handout key: it has no URL of its own.
_UPLOAD_KEY_PREFIX = "author:"


class AddResult(enum.Enum):
    ADDED = "added"
    FULL = "full"  # MAX_AUTHOR_SCREENSHOTS already on the list
    DUPLICATE = "duplicate"  # the main screenshot's frame, an entry's frame, or URL


def entries(session: Session, game_id: int) -> list[ClueAuthorScreenshot]:
    """The game's list, in the order the author added it."""
    stmt = (
        select(ClueAuthorScreenshot)
        .where(ClueAuthorScreenshot.game_id == game_id)
        .order_by(ClueAuthorScreenshot.id)
    )
    return list(session.scalars(stmt))


def _main_fingerprint(game: Game) -> int | None:
    if game.original_image is None:
        return None
    try:
        return fingerprint.fingerprint(game.original_image)
    except ValueError:
        logger.opt(exception=True).warning("could not fingerprint the main screenshot")
        return None


def _duplicate_of(
    game: Game, listed: list[ClueAuthorScreenshot], print_: int, url: str | None
) -> str | None:
    """What an addition would duplicate, if anything."""
    main = _main_fingerprint(game)
    if main is not None and fingerprint.same_frame(print_, main):
        return "the main screenshot"
    for number, entry in enumerate(listed, start=1):
        if fingerprint.same_frame(print_, entry.fingerprint) or (url and entry.url == url):
            return f"entry {number}"
    return None


@dataclass(frozen=True)
class NewEntry:
    """A screenshot to add: a gallery pick (`source` and `url` set) or an
    upload (both None)."""

    image: bytes
    source: Provider | None = None
    url: str | None = None


def add(session: Session, game: Game, new: NewEntry) -> AddResult:
    """Add a screenshot to the game's list. Refused as a duplicate of the
    main screenshot or of an entry (the same frame, or the same URL), and
    once the list holds MAX_AUTHOR_SCREENSHOTS. Raises ValueError if
    `new.image` isn't a readable image."""
    image, source, url = new.image, new.source, new.url
    print_ = fingerprint.fingerprint(image)
    what = url or "an upload"
    listed = entries(session, game.id)
    duplicate = _duplicate_of(game, listed, print_, url)
    if duplicate is not None:
        logger.warning(
            "refused {what} for the author's list: same as {duplicate}",
            what=what,
            duplicate=duplicate,
            game_id=game.id,
        )
        return AddResult.DUPLICATE
    if len(listed) >= MAX_AUTHOR_SCREENSHOTS:
        logger.warning(
            "refused {what} for the author's list: full ({limit})",
            what=what,
            limit=MAX_AUTHOR_SCREENSHOTS,
            game_id=game.id,
        )
        return AddResult.FULL
    session.add(
        ClueAuthorScreenshot(
            game_id=game.id, source=source, url=url, image=image, fingerprint=print_
        )
    )
    session.flush()
    logger.info(
        "added {what} to the author's list ({count}/{limit})",
        what=what,
        count=len(listed) + 1,
        limit=MAX_AUTHOR_SCREENSHOTS,
        source=source,
        game_id=game.id,
    )
    return AddResult.ADDED


def remove(session: Session, game_id: int, entry_id: int) -> bool:
    """Remove one entry of the game's list; False if it has no such entry."""
    stmt = delete(ClueAuthorScreenshot).where(
        ClueAuthorScreenshot.game_id == game_id, ClueAuthorScreenshot.id == entry_id
    )
    removed = cast(CursorResult, session.execute(stmt)).rowcount > 0
    if removed:
        logger.info(
            "removed entry {entry_id} from the author's list", entry_id=entry_id, game_id=game_id
        )
    else:
        logger.warning(
            "no entry {entry_id} on the author's list to remove", entry_id=entry_id, game_id=game_id
        )
    return removed


def handout_key(entry: ClueAuthorScreenshot) -> str:
    """What the entry is handed out under (`screenshot_url` on the purchase
    and its handout row): its source URL, so a random pick of the same URL
    is never sold twice, or `author:<id>` for an upload, which has none."""
    return entry.url or f"{_UPLOAD_KEY_PREFIX}{entry.id}"


def unsold(session: Session, game: Game) -> list[ClueAuthorScreenshot]:
    """The entries the shop may still sell: not handed out in the game yet,
    and not the round's own screenshot."""
    taken = set(game.shown_screenshot_urls or [])
    taken |= {row.screenshot_url for row in handouts.for_game(session, game.id)}
    return [entry for entry in entries(session, game.id) if handout_key(entry) not in taken]


def clear_images(session: Session, game_id: int) -> int:
    """Drop the kept originals once the game is revealed: every one handed
    out has its own copy on its handout row. Returns the rows cleared."""
    stmt = (
        update(ClueAuthorScreenshot)
        .where(ClueAuthorScreenshot.game_id == game_id, ClueAuthorScreenshot.image.is_not(None))
        .values(image=None)
    )
    cleared = cast(CursorResult, session.execute(stmt)).rowcount
    logger.debug("cleared {count} author's list image(s)", count=cleared, game_id=game_id)
    return cleared


def reset(session: Session, game: Game) -> None:
    """Forget the list and go back to Random mode: the anime is being
    identified again, and the list's screenshots belong to the old one."""
    stmt = delete(ClueAuthorScreenshot).where(ClueAuthorScreenshot.game_id == game.id)
    removed = cast(CursorResult, session.execute(stmt)).rowcount
    previous = game.clue_screenshot_mode
    game.clue_screenshot_mode = ClueScreenshotMode.RANDOM
    if not removed and previous == ClueScreenshotMode.RANDOM:
        logger.debug("no author's list to reset", game_id=game.id)
        return
    logger.info(
        "reset the author's list ({count} entries removed, mode {previous} -> random)",
        count=removed,
        previous=previous,
        game_id=game.id,
    )


def forget(game: Game) -> None:
    """`reset` for a caller with no session at hand (identification
    clearing): the game's own session, or, for a game not saved yet, just
    the mode, as there is no list to delete."""
    session = object_session(game)
    if session is None or game.id is None:
        game.clue_screenshot_mode = ClueScreenshotMode.RANDOM
        return
    reset(session, game)


def drop_matching_main(session: Session, game: Game) -> int:
    """Remove the entries that are now the same frame as the main
    screenshot ("Change image" since they were added). Run at Confirm.
    Returns how many went."""
    main = _main_fingerprint(game)
    if main is None:
        return 0
    matching = [
        entry.id
        for entry in entries(session, game.id)
        if fingerprint.same_frame(entry.fingerprint, main)
    ]
    if matching:
        session.execute(delete(ClueAuthorScreenshot).where(ClueAuthorScreenshot.id.in_(matching)))
        logger.info(
            "dropped {count} author's list screenshot(s) matching the main screenshot",
            count=len(matching),
            game_id=game.id,
        )
    return len(matching)


def needs_empty_warning(session: Session, game: Game) -> bool:
    """Whether Confirm should warn first: the mode wants the author's list,
    but it is empty."""
    return game.clue_screenshot_mode != ClueScreenshotMode.RANDOM and not entries(session, game.id)
