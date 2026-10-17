"""Which screenshots the clue shop already handed out in a game (issue #446).
A screenshot goes out at most once per game: not again to the same buyer,
not to another player, and not after a refund. shop.purchase records each
one; the screenshot picker (commands/shop/images.py) skips them."""

from typing import cast

from loguru import logger
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.clue_screenshot_handout import ClueScreenshotHandout
from nani_pix_bot.models.enums import ClueKind


def for_game(session: Session, game_id: int) -> list[ClueScreenshotHandout]:
    stmt = (
        select(ClueScreenshotHandout)
        .where(ClueScreenshotHandout.game_id == game_id)
        .order_by(ClueScreenshotHandout.id)
    )
    return list(session.scalars(stmt))


def is_handed_out(session: Session, game_id: int, url: str) -> bool:
    stmt = select(ClueScreenshotHandout.id).where(
        ClueScreenshotHandout.game_id == game_id, ClueScreenshotHandout.screenshot_url == url
    )
    return session.scalars(stmt).first() is not None


def sellable(session: Session, game_id: int, url: str | None) -> bool:
    """Whether a screenshot request names a URL nobody got in this game yet.
    Checked before the charge, this is what keeps (game, URL) unique: a
    later buyer of the same URL is refused, uncharged."""
    if not url:
        return False
    return not is_handed_out(session, game_id, url)


def record(
    session: Session, bought: CluePurchase, fingerprint: int | None, image: bytes | None
) -> None:
    """Record the screenshot of a just-made purchase; other kinds have none."""
    url = bought.screenshot_url
    if bought.kind != ClueKind.SCREENSHOT or url is None:
        return
    session.add(
        ClueScreenshotHandout(
            game_id=bought.game_id, screenshot_url=url, fingerprint=fingerprint, image=image
        )
    )
    session.flush()
    logger.debug("recorded screenshot handout {url}", url=url, game_id=bought.game_id)


def revealed_images(session: Session, game_id: int) -> list[bytes]:
    """The kept originals of a game, in handout order. Refunded purchases
    count: the buyer saw the image, so the album shows it too."""
    stmt = (
        select(ClueScreenshotHandout.image)
        .where(ClueScreenshotHandout.game_id == game_id, ClueScreenshotHandout.image.is_not(None))
        .order_by(ClueScreenshotHandout.id)
    )
    return [image for image in session.scalars(stmt) if image is not None]


def clear_images(session: Session, game_id: int) -> int:
    """Drop the kept originals once the album is out; the rows stay so a
    screenshot is still never handed out twice. Returns the rows cleared."""
    stmt = (
        update(ClueScreenshotHandout)
        .where(ClueScreenshotHandout.game_id == game_id, ClueScreenshotHandout.image.is_not(None))
        .values(image=None)
    )
    cleared = cast(CursorResult, session.execute(stmt)).rowcount
    logger.debug("cleared {count} handout image(s)", count=cleared, game_id=game_id)
    return cleared
