"""TEMPORARY diagnostic command — DELETE ME (and testpixels_algos.py,
and its registration in app.py, and the tmp/example*.* images) once the
obfuscation-algorithm question is answered. Posts the example images
under one or every candidate algorithm so they can be compared visually
before anything is promoted into services/pixelate.py for real. Open to
anyone who can message the bot — not worth i18n/test investment given
its lifespan.

    /testpixels                       live algorithm at the live stage widths
    /testpixels <algo>                that algorithm at the live stage widths
    /testpixels <algo> 32,64,128      that algorithm at raw widths
    /testpixels all <stage 1-5>       every algorithm at that stage's width
    /testpixels 32,64                 (still works) live algorithm, raw widths

Every algorithm takes a target width, so `all` can anchor to a stage
number and have one setting mean the same thing across all of them.

Sends are paced deliberately: Telegram caps a media group at 10 items
and throttles a *group* chat at roughly 20 messages a minute, and a
batch run is exactly the shape of request that trips flood control. So
`all` mode posts albums per example image (each holding that scene under
every algorithm) rather than per algorithm, keeping a full comparison to
a handful of sends — and the gap between albums is much larger in a
group than in a DM, where the limit is far more forgiving.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from pathlib import Path

from loguru import logger
from telegram import InputMediaPhoto, Update
from telegram.constants import ChatType
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.testpixels_algos import ALGORITHMS, render
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.services.settings import stage_config

_EXAMPLE_IMAGES = [Path("tmp/example1.jpg"), Path("tmp/example2.png")]
_DEFAULT_ALGO = "nearest"

# Telegram's own media-group cap.
_ALBUM_MAX = 10
# A group chat throttles at roughly 20 messages/minute; a DM is far more
# forgiving, so a diagnostic run there needn't crawl. The worst case is
# a full-width sweep, _MAX_WIDTHS albums of one photo per example image,
# which these gaps keep under that ceiling.
_GAP_PRIVATE_SECONDS = 2.0
_GAP_GROUP_SECONDS = 8.0
# Refuse oversized single-algo sweeps rather than half-send one that
# then gets throttled mid-run.
_MAX_WIDTHS = 8


@dataclass(frozen=True)
class _Shot:
    algo_name: str
    width: int
    image_path: Path
    caption: str | None


def _parse_widths(args: list[str]) -> list[int] | None:
    """Positive ints from comma- and/or space-separated tokens. `[]` when
    nothing was given, `None` when what was given isn't usable."""
    tokens = [token for arg in args for token in arg.split(",") if token.strip()]
    if not tokens:
        return []
    try:
        values = {int(token) for token in tokens}
    except ValueError:
        return None
    if any(value <= 0 for value in values):
        return None
    return sorted(values)


def _usage() -> str:
    lines = [
        "/testpixels — live algorithm at the live stage widths",
        "/testpixels <algo> [w,w,...] — one algorithm, live stage widths if no numbers",
        "/testpixels all <stage 1-5> — every algorithm at that stage's width",
        f"(at most {_MAX_WIDTHS} widths per run, to stay under Telegram's flood limits)",
        "",
        "Algorithms:",
    ]
    lines += [f"  {name} — {algo.help}" for name, algo in ALGORITHMS.items()]
    return "\n".join(lines)


def _chunked(shots: list[_Shot]) -> list[list[_Shot]]:
    """Split into albums of at most _ALBUM_MAX, as evenly as possible (a
    hypothetical 11 would become 6 and 5, not 10 and a lonely 1). The
    current roster fits one album, but truncating instead of chunking
    would silently drop an algorithm the moment it doesn't."""
    albums = max(1, ceil(len(shots) / _ALBUM_MAX))
    size = ceil(len(shots) / albums)
    return [shots[index : index + size] for index in range(0, len(shots), size)]


def _plan_single(algo_name: str, widths: list[int]) -> list[list[_Shot]]:
    """One album per width, holding every example image at it — the
    original /testpixels shape."""
    return [
        [
            _Shot(algo_name, width, path, f"{algo_name} w={width}" if index == 0 else None)
            for index, path in enumerate(_EXAMPLE_IMAGES)
        ]
        for width in widths
    ]


def _plan_all(width: int) -> list[list[_Shot]]:
    """Albums per example image, holding that scene under every
    algorithm — swiping an album is the side-by-side comparison."""
    plan = []
    for path in _EXAMPLE_IMAGES:
        shots = [_Shot(name, width, path, f"{name} w={width}") for name in ALGORITHMS]
        plan.extend(_chunked(shots))
    return plan


def _build_plan(args: list[str], stage_widths: list[int]) -> list[list[_Shot]] | None:
    """The albums to post, or None if `args` don't make sense."""
    if args and args[0].lower() == "all":
        stage = _parse_widths(args[1:])
        if not stage or len(stage) != 1 or stage[0] > len(stage_widths):
            return None
        return _plan_all(stage_widths[stage[0] - 1])

    algo_name, rest = _DEFAULT_ALGO, args
    if args and args[0].lower() in ALGORITHMS:
        algo_name, rest = args[0].lower(), args[1:]

    widths = _parse_widths(rest)
    if widths is None or len(widths) > _MAX_WIDTHS:
        return None
    return _plan_single(algo_name, widths or stage_widths)


async def _send_album(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    thread_id: int | None,
    media: list[InputMediaPhoto],
) -> None:
    async def post() -> None:
        await context.bot.send_media_group(
            chat_id=chat_id, message_thread_id=thread_id, media=media
        )

    try:
        await post()
    except RetryAfter as exc:
        # python-telegram-bot types this as int | timedelta.
        after = exc.retry_after
        delay = after.total_seconds() if isinstance(after, timedelta) else after
        logger.warning("testpixels: flood-limited, waiting {}s before one retry", delay)
        await asyncio.sleep(delay + 1)
        try:
            await post()
        except TelegramError as retry_exc:
            logger.error("testpixels: album failed even after the retry: {}", retry_exc)
    except TelegramError as exc:
        logger.error("testpixels: album send failed: {}", exc)


async def _render_album(group: list[_Shot]) -> list[InputMediaPhoto]:
    media = []
    for shot in group:
        if not shot.image_path.exists():
            logger.warning("testpixels: missing example image {}", shot.image_path)
            continue
        data = shot.image_path.read_bytes()
        # Rank filters and big upscales are slow enough to stall the
        # event loop for seconds, which would freeze the live bot.
        image = await asyncio.to_thread(render, shot.algo_name, data, shot.width)
        media.append(InputMediaPhoto(media=image, caption=shot.caption))
    return media


async def testpixels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        config = stage_config.get_stage_config(session)
    stage_widths = [config[stage].target_width for stage in PixelStage]

    args = context.args or []
    plan = _build_plan(args, stage_widths)
    if plan is None:
        await message.reply_text(_usage())
        return

    gap = _GAP_PRIVATE_SECONDS if chat.type == ChatType.PRIVATE else _GAP_GROUP_SECONDS
    for index, group in enumerate(plan):
        media = await _render_album(group)
        if not media:
            continue
        if index:
            await asyncio.sleep(gap)
        await _send_album(context, chat.id, message.message_thread_id, media)

    logger.info(
        "{} ran /testpixels {} -> {} albums, {}s gap",
        user.id,
        " ".join(args) or "(defaults)",
        len(plan),
        gap,
    )
