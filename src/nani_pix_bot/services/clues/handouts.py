"""Which screenshots the clue shop already handed out in a game (issue #446).
A screenshot goes out at most once per game: not again to the same buyer,
not to another player, and not after a refund. shop.purchase records each
one; the screenshot picker (commands/shop/images.py) skips them."""

from loguru import logger
from sqlalchemy import select
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


def record(session: Session, bought: CluePurchase, fingerprint: int | None) -> None:
    """Record the screenshot of a just-made purchase; other kinds have none."""
    url = bought.screenshot_url
    if bought.kind != ClueKind.SCREENSHOT or url is None:
        return
    session.add(
        ClueScreenshotHandout(game_id=bought.game_id, screenshot_url=url, fingerprint=fingerprint)
    )
    session.flush()
    logger.debug("recorded screenshot handout {url}", url=url, game_id=bought.game_id)
