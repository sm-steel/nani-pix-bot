"""TEMPORARY diagnostic command — DELETE ME (and testpixels_algos.py,
and its registration in app.py, and the tmp/example*.* images) once the
obfuscation-algorithm question is answered. Posts the example images
under one or every candidate algorithm so they can be compared visually
before anything is promoted into services/pixelate.py for real. Open to
anyone who can message the bot — not worth i18n/test investment given
its lifespan.

    /testpixels                       live algorithm at the live stage widths
    /testpixels <algo>                that algorithm at its own stage defaults
    /testpixels <algo> 32,64,128      that algorithm at raw params
    /testpixels all <stage 1-5>       every algorithm at that stage's setting
    /testpixels 32,64                 (still works) live algorithm, raw widths

`all` is anchored to a stage number rather than a shared parameter
because the two families' parameters are not comparable — a target
width of 64 and a blur radius of 64 mean nothing alike. Anchoring to
the game's own stages keeps the comparison honest.

Sends are paced deliberately: Telegram caps a media group at 10 items
and throttles a group chat at roughly 20 messages a minute, and a batch
run is exactly the shape of request that trips flood control. `all`
mode therefore posts one album per example image (each holding that
scene under every algorithm) rather than one per algorithm, which keeps
a full comparison down to two sends.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from loguru import logger
from telegram import InputMediaPhoto, Update
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
# Pause between albums, well inside a group chat's ~20 messages/minute.
_SEND_GAP_SECONDS = 3.0
# Refuse oversized single-algo sweeps rather than half-send one that
# then gets throttled mid-run.
_MAX_PARAMS = 8


@dataclass(frozen=True)
class _Shot:
    algo_name: str
    param: int
    image_path: Path
    caption: str | None


def _parse_params(args: list[str]) -> list[int] | None:
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
        "/testpixels <algo> [n,n,...] — one algorithm, its stage defaults if no numbers",
        "/testpixels all <stage 1-5> — every algorithm at that stage's setting",
        f"(at most {_MAX_PARAMS} numbers per run, to stay under Telegram's flood limits)",
        "",
        "Algorithms:",
    ]
    lines += [f"  {name} <{algo.param_kind}> — {algo.help}" for name, algo in ALGORITHMS.items()]
    return "\n".join(lines)


def _label(algo_name: str, param: int) -> str:
    return f"{algo_name} {ALGORITHMS[algo_name].param_kind}={param}"


def _plan_single(algo_name: str, params: list[int]) -> list[list[_Shot]]:
    """One album per parameter, holding every example image at it —
    the original /testpixels shape."""
    return [
        [
            _Shot(algo_name, param, path, _label(algo_name, param) if index == 0 else None)
            for index, path in enumerate(_EXAMPLE_IMAGES)
        ]
        for param in params
    ]


def _plan_all(stage_width: int) -> list[list[_Shot]]:
    """One album per example image, holding that scene under every
    algorithm — swiping an album is the side-by-side comparison."""
    settings = [(name, algo.default_param(stage_width)) for name, algo in ALGORITHMS.items()]
    return [
        [_Shot(name, param, path, _label(name, param)) for name, param in settings]
        for path in _EXAMPLE_IMAGES
    ]


def _build_plan(args: list[str], stage_widths: list[int]) -> list[list[_Shot]] | None:
    """The albums to post, or None if `args` don't make sense."""
    if args and args[0].lower() == "all":
        stage = _parse_params(args[1:])
        if not stage or len(stage) != 1 or stage[0] > len(stage_widths):
            return None
        return _plan_all(stage_widths[stage[0] - 1])

    algo_name, rest = _DEFAULT_ALGO, args
    if args and args[0].lower() in ALGORITHMS:
        algo_name, rest = args[0].lower(), args[1:]

    params = _parse_params(rest)
    if params is None or len(params) > _MAX_PARAMS:
        return None
    if not params:
        params = [ALGORITHMS[algo_name].default_param(width) for width in stage_widths]
    elif ALGORITHMS[algo_name].param_kind == "radius":
        # A sweep should read hardest-to-clearest like the game's own
        # stage order does. For a target width that is ascending; for a
        # blur radius it is the other way round.
        params.reverse()
    return _plan_single(algo_name, params)


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
    for shot in group[:_ALBUM_MAX]:
        if not shot.image_path.exists():
            logger.warning("testpixels: missing example image {}", shot.image_path)
            continue
        data = shot.image_path.read_bytes()
        # Rank filters and big upscales are slow enough to stall the
        # event loop for seconds, which would freeze the live bot.
        image = await asyncio.to_thread(render, shot.algo_name, data, shot.param)
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

    for index, group in enumerate(plan):
        media = await _render_album(group)
        if not media:
            continue
        if index:
            await asyncio.sleep(_SEND_GAP_SECONDS)
        await _send_album(context, chat.id, message.message_thread_id, media)

    logger.info(
        "{} ran /testpixels {} -> {} albums", user.id, " ".join(args) or "(defaults)", len(plan)
    )
