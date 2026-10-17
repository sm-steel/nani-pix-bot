"""The author's clue screenshot list (issue #450): the screenshots a game's
starter picked from a Shikimori/TMDB gallery or uploaded on the setup
preview's clue panel, which the shop's "Extra screenshot" clue sells from
according to `Game.clue_screenshot_mode`. Only changeable during setup;
frozen once the game is live. See MECHANICS.md's "Clue shop"."""

from typing import cast

from loguru import logger
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from nani_pix_bot.models.clue_author_screenshot import ClueAuthorScreenshot
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import handouts

# The prefix of an uploaded entry's handout key: it has no URL of its own.
_UPLOAD_KEY_PREFIX = "author:"


def entries(session: Session, game_id: int) -> list[ClueAuthorScreenshot]:
    """The game's list, in the order the author added it."""
    stmt = (
        select(ClueAuthorScreenshot)
        .where(ClueAuthorScreenshot.game_id == game_id)
        .order_by(ClueAuthorScreenshot.id)
    )
    return list(session.scalars(stmt))


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
    logger.info("cleared {count} author's clue screenshot image(s)", count=cleared, game_id=game_id)
    return cleared
