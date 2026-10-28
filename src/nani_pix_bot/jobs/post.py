"""One announcement as it will be sent: text, optional card, where it goes,
and how one goes out (the card or text, then its rich message if it has one)."""

from dataclasses import dataclass
from typing import TypedDict

from loguru import logger
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.rich import RichTarget, send_rich
from nani_pix_bot.services.cards import GoalCard, PodiumCard, SeasonBanner, UnlockCard


@dataclass(frozen=True)
class Post:
    row_ids: tuple[int, ...]
    text: str
    card: UnlockCard | PodiumCard | SeasonBanner | GoalCard | None = None
    avatar_ids: tuple[int, ...] = ()
    batch_id: int | None = None
    background_slot: str | None = None
    background: bytes | None = None
    dm_to: int | None = None  # a player's DM instead of the topic
    # Rich-message markdown sent right after (the season guide). Only a post
    # with a batch_id joins an album, so a post with `rich` is always sent alone.
    rich: str | None = None


class Where(TypedDict, total=False):
    chat_id: int
    message_thread_id: int


def where(context: ContextTypes.DEFAULT_TYPE, post: Post) -> Where:
    if post.dm_to is not None:
        return {"chat_id": post.dm_to}
    return {
        "chat_id": context.bot_data["group_chat_id"],
        "message_thread_id": context.bot_data["game_topic_id"],
    }


async def send_one(context: ContextTypes.DEFAULT_TYPE, post: Post, image: bytes | None) -> None:
    """A photo with the caption, or the text alone when there's no card,
    then the post's rich message (the season guide), if it has one."""
    if not post.text:
        return
    if image is None:
        await context.bot.send_message(text=post.text, **where(context, post))
    else:
        await context.bot.send_photo(photo=image, caption=post.text, **where(context, post))
    if post.rich is not None:
        target = where(context, post)
        rich_target = RichTarget(target["chat_id"], target.get("message_thread_id"))
        await send_rich(context.bot, rich_target, post.rich)
        logger.info("posted the rich message of announcement {outbox_ids}", outbox_ids=post.row_ids)
