"""The shared "Nani Pix claims a game" routine behind every bot-started
game: the idle autostart, the overthrow (autostart.py) and the season's
Gashadokuro boss (boss.py). Gathers a random gated pick and turns it into
an ACTIVE game with its first post — HARD MODE for the first two, a normal
5-stage game for the boss."""

import enum
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal
from nani_pix_bot.jobs.timers.current_image import post_stage_image, post_stage_images
from nani_pix_bot.jobs.timers.game_timeout import schedule_timeout
from nani_pix_bot.jobs.timers.inactivity import schedule_inactivity_timers
from nani_pix_bot.jobs.timers.turn_timers import cancel_turn_timers
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.cosmetics import stage as stage_cosmetics
from nani_pix_bot.services.game import autostart as autostart_service
from nani_pix_bot.services.game.autostart import GatheredPick
from nani_pix_bot.services.reveal import store as reveal_store
from nani_pix_bot.services.search import tags as anime_tags
from nani_pix_bot.services.seasons import gate as gate_service
from nani_pix_bot.services.seasons import group_goal, haunting, modifiers, multipliers
from nani_pix_bot.services.settings import stage_config

IDLE_AUTOSTART_JOB_NAME = "idle-autostart"


class AutostartTrigger(enum.Enum):
    OVERTHROW = "overthrow"
    IDLE = "idle"
    BOSS = "boss"


@dataclass(frozen=True)
class _FirstTurnPost:
    """What the group announcement needs, captured while the staging
    game's session was still open — the post goes out after that session
    (and its commit) has already closed, same pattern as
    commands/dm_start/preview.py's _FirstStagePost. A HARD MODE game
    carries the screenshot pair's two pixelated photos; the boss game,
    a normal 5-stage game, carries one."""

    photos: tuple[bytes, ...]
    caption: str


@dataclass(frozen=True)
class _Gathered:
    """A live pick, and whether the season gate shaped it (for the log)."""

    pick: GatheredPick
    gated: bool


@dataclass(frozen=True)
class AutostartClaim:
    """Everything run_bot_autostart and _build_first_turn_post need
    beyond a live pick, bundled into one parameter so neither function's
    own signature grows past qlty's too-many-parameters threshold.

    `expected_next_starter_id` is turn_state.next_starter_id as the
    caller last observed it before deciding to attempt this autostart —
    see run_bot_autostart's docstring for why that (not "must be None")
    is the actual race-safety invariant."""

    trigger: AutostartTrigger
    dethroned_winner_name: str | None
    expected_next_starter_id: int | None = None


def cancel_idle_autostart(job_queue: JobQueue | None) -> None:
    if job_queue is None:
        return
    logger.debug("canceling idle-autostart timer")
    for job in job_queue.get_jobs_by_name(IDLE_AUTOSTART_JOB_NAME):
        job.schedule_removal()


def describe_holder(session: Session, next_starter_id: int | None) -> str:
    """Who holds the turn, for a log line — `nobody (turn open)` for None."""
    if next_starter_id is None:
        return "nobody (turn open)"
    return players.describe_player_id(session, next_starter_id)


def _dethroned_label(session: Session, claim: AutostartClaim) -> str:
    """The overthrown winner's name for the group caption, with their flair."""
    name = claim.dethroned_winner_name or ""
    if claim.expected_next_starter_id is None:
        return name
    return flair.named(session, claim.expected_next_starter_id, name)


def _build_first_turn_post(
    session: Session,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    lang: str,
    claim: AutostartClaim,
) -> _FirstTurnPost:
    """Every autostart game is hard mode now — activate_game() must run
    first (it's what sets game.hard_mode_turn = 1), since
    hard_mode_turn_width()/hard_mode_turn_progress() below both read
    that field. Contrast the old stage-based version of this function,
    where the equivalent pixelation-width lookup (stage_config, keyed by
    STAGE_ORDER[0]) didn't depend on activate_game() having run yet."""
    game_service.activate_game(session, game)
    if game.hard_mode_image_a is None or game.hard_mode_image_b is None:
        raise RuntimeError("game.hard_mode_image_a/_b is None in _build_first_turn_post")
    width = game_service.hard_mode_turn_width(game)
    overthrow = claim.trigger is AutostartTrigger.OVERTHROW
    pixelated_a = stage_cosmetics.stage_png(
        session, game, game.hard_mode_image_a, width, overthrow=overthrow
    )
    pixelated_b = stage_cosmetics.stage_png(
        session, game, game.hard_mode_image_b, width, overthrow=overthrow
    )
    progress = game_service.hard_mode_turn_progress(game)
    caption_kwargs = {
        "turn": progress.number,
        "total": progress.total,
        "remaining": progress.remaining,
        "limit": progress.limit,
    }
    if claim.trigger is AutostartTrigger.IDLE:
        caption = i18n.t("dm_start.hard_mode_game_started_caption_idle", lang, **caption_kwargs)
    elif claim.dethroned_winner_name is not None:
        caption = i18n.t(
            "dm_start.hard_mode_game_started_caption_overthrow_winner",
            lang,
            winner=_dethroned_label(session, claim),
            **caption_kwargs,
        )
    else:
        caption = i18n.t(
            "dm_start.hard_mode_game_started_caption_overthrow_open", lang, **caption_kwargs
        )
    if game.hard_mode_clue_discount:
        caption += "\n" + i18n.t(
            "hard_mode.discount_line", lang, percent=game.hard_mode_clue_discount
        )
    caption += haunting.caption_suffix(
        session, game, lang, number=progress.number, total=progress.total
    )
    caption += game_service.game_id_line(game.id, lang)
    schedule_timeout(context.job_queue, game)
    schedule_inactivity_timers(context.job_queue, game)
    return _FirstTurnPost(photos=(pixelated_a, pixelated_b), caption=caption)


def _build_boss_post(
    session: Session, context: ContextTypes.DEFAULT_TYPE, game: Game, lang: str
) -> _FirstTurnPost:
    """The boss game's stage 1, posted the way a human game's is (see
    preview.py's _activate_and_stage_first_post): rendered only after
    activation, which tags the season its twists and style depend on."""
    game_service.activate_game(session, game)
    if game.original_image is None:
        raise RuntimeError("game.original_image is None in _build_boss_post")
    total = len(game_service.STAGE_ORDER)
    width = stage_config.get_stage_config(session, game_service.STAGE_ORDER[0]).target_width
    photo = stage_cosmetics.stage_png(session, game, game.original_image, width)
    run = modifiers.season_of(session, game)
    caption = i18n.t(
        "dm_start.boss_game_started_caption",
        lang,
        xp=1 if run is None else multipliers.xp_multiplier(run, game),
        coins=1 if run is None else multipliers.coin_multiplier(run, game),
    )
    caption += haunting.caption_suffix(session, game, lang, number=1, total=total)
    caption += game_service.game_id_line(game.id, lang)
    schedule_timeout(context.job_queue, game)
    schedule_inactivity_timers(context.job_queue, game)
    return _FirstTurnPost(photos=(photo,), caption=caption)


async def run_bot_autostart(
    context: ContextTypes.DEFAULT_TYPE, session_factory, claim: AutostartClaim
) -> bool:
    """The shared "bot claims a game" routine both triggers delegate to
    once a valid pick is in hand. Returns whether a game was actually
    started. Deliberately doesn't reuse commands/dm_start/preview.py's
    private _activate_and_stage_first_post(): that function lives inside
    a DM-flow module jobs/ shouldn't import from, and a bot-started game
    never passes through SETUP/setup-abandon the way a human one does
    (no cancel_setup_abandon call here — none was ever scheduled).

    `claim.expected_next_starter_id` is what the caller already knew
    `turn_state.next_starter_id` to be when it decided to attempt this
    autostart — None for idle_autostart_job_callback (it only fires when
    the turn is open), or the about-to-be-dethroned winner_id for
    maybe_overthrow (None too, for an unsolved/timeout ending that left
    the turn open). It is deliberately *not* "must be None": overthrowing
    a winner who still legitimately holds the turn is this feature's
    whole point, so the real invariant to guard is "nothing changed the
    turn's ownership while gather_pick()'s several real HTTP round-trips
    were in flight" — e.g. a human's /skip @user landing mid-flight and
    designating someone gather_pick() never knew about. activate_game()
    unconditionally clears next_starter_id, so proceeding on a mismatch
    would silently steal a turn nobody agreed to give up."""
    gathered = await gather(context, session_factory, claim.trigger)
    if gathered is None:
        return False
    return await claim_game(context, session_factory, claim, gathered)


async def gather(
    context: ContextTypes.DEFAULT_TYPE, session_factory, trigger: AutostartTrigger
) -> _Gathered | None:
    """A random pick from the running season's gate, or None (logged)."""
    search_client = context.bot_data["search_client"]
    tmdb_client = context.bot_data["tmdb_client"]
    tenrai_client = context.bot_data["tenrai_client"]
    with session_scope(session_factory) as session:
        gate = gate_service.active_gate(session)
    pick = await autostart_service.gather_pick(search_client, tmdb_client, tenrai_client, gate=gate)
    if pick is None:
        logger.warning(
            "bot autostart ({trigger}) found no usable pick after {attempts} attempt(s) — "
            "skipping this firing",
            trigger=trigger.value,
            attempts=autostart_service.AUTOSTART_ATTEMPT_LIMIT,
            gated=gate is not None,
        )
        return None
    return _Gathered(pick, gated=gate is not None)


def _claim_blocked(session: Session, claim: AutostartClaim) -> bool:
    """Whether something changed while the pick was gathered (logged)."""
    if game_service.active_or_setup_game(session) is not None:
        logger.warning(
            "bot autostart ({trigger}) aborted — a game was started in the meantime",
            trigger=claim.trigger.value,
        )
        return True
    turn_state = game_service.get_turn_state(session)
    actual_next_starter_id = turn_state.next_starter_id if turn_state is not None else None
    if actual_next_starter_id != claim.expected_next_starter_id:
        logger.warning(
            "bot autostart ({trigger}) aborted — turn ownership changed in the meantime "
            "(expected the turn held by {expected_holder}, now {holder})",
            trigger=claim.trigger.value,
            expected_holder=describe_holder(session, claim.expected_next_starter_id),
            holder=describe_holder(session, actual_next_starter_id),
        )
        return True
    return False


def _stage_hard_mode(session: Session, game: Game, pick: GatheredPick) -> None:
    """Every overthrow/idle pick is a HARD MODE game: original_image stays
    None, the screenshot pair lives in hard_mode_image_a/_b instead."""
    game.hard_mode = True
    game.hard_mode_clue_discount = game_service.next_clue_discount(session)
    logger.info(
        "HARD MODE clue discount frozen at {percent}%",
        percent=game.hard_mode_clue_discount,
        game_id=game.id,
    )
    game.hard_mode_image_a = pick.screenshot.image_bytes_a
    game.hard_mode_image_b = pick.screenshot.image_bytes_b
    game.shown_screenshot_urls = [pick.screenshot.url_a, pick.screenshot.url_b]


def _stage_boss(session: Session, game: Game, pick: GatheredPick) -> bool:
    """The boss is a normal game on the pair's first screenshot, marked the
    season's boss before activation. False when another boss got there
    first (mark_boss re-checks under the season lock)."""
    game.hard_mode = False
    game.original_image = pick.screenshot.image_bytes_a
    game.shown_screenshot_urls = [pick.screenshot.url_a]
    group_goal.mark_boss(session, game)
    return game.boss_game


def _stage_pick(session: Session, game: Game, claim: AutostartClaim, pick: GatheredPick) -> bool:
    """Puts the pick on the SETUP game. False when the claim must stop."""
    game_service.stage_result(game, pick.anime.result, source=pick.anime.source)
    if pick.anime.tags:
        game.anime_tags = anime_tags.to_json(list(pick.anime.tags))
        logger.info(
            "season gate: stored tags for {pick!r}",
            pick=pick.anime.result,
            tags=[t.name for t in pick.anime.tags],
            game_id=game.id,
        )
    game.screenshot_source = pick.screenshot.provider
    setattr(game, pick.screenshot.provider.id_attr_name, pick.screenshot.provider_id)
    if claim.trigger is AutostartTrigger.BOSS:
        return _stage_boss(session, game, pick)
    _stage_hard_mode(session, game, pick)
    return True


async def claim_game(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory,
    claim: AutostartClaim,
    gathered: _Gathered,
) -> bool:
    """Claims the game with a pick in hand (see run_bot_autostart)."""
    pick = gathered.pick
    with session_scope(session_factory) as session:
        if _claim_blocked(session, claim):
            return False
        lang = settings.get_language(session)
        players.get_or_create_player(
            session, context.bot.id, username=context.bot_data.get("bot_username"), grant=False
        )
        game = game_service.create_setup_game(
            session, starter_id=context.bot.id, entry=f"bot autostart ({claim.trigger.value})"
        )
        if not _stage_pick(session, game, claim, pick):
            logger.warning("boss game start aborted — another boss got there first")
            session.delete(game)
            return False
        game_service.clear_turn_timers(session)
        if claim.trigger is AutostartTrigger.BOSS:
            first_turn_post = _build_boss_post(session, context, game, lang)
            image_choice = None
        else:
            first_turn_post = _build_first_turn_post(session, context, game, lang, claim)
            image_choice = reveal_store.pick_image()
        reveal_store.reserve(session, game.id, reveal_store.pick_effect(), image_choice)
        game_service.clear_autostart(session)
        game_id = game.id

    cancel_turn_timers(context.job_queue)
    cancel_idle_autostart(context.job_queue)
    logger.info(
        "claimed by bot autostart ({trigger}) — anime source={source}, "
        "screenshot provider={provider}",
        trigger=claim.trigger.value,
        source=pick.anime.source,
        provider=pick.screenshot.provider,
        gated=gathered.gated,
        game_id=game_id,
    )
    reveal.start_pregeneration(context.application, game_id)
    await _post_first_turn(context, session_factory, first_turn_post)
    return True


async def _post_first_turn(
    context: ContextTypes.DEFAULT_TYPE, session_factory, post: _FirstTurnPost
) -> None:
    if len(post.photos) == 2:
        photos = (post.photos[0], post.photos[1])
        await post_stage_images(context, session_factory, photos=photos, caption=post.caption)
        return
    await post_stage_image(context, session_factory, photo=post.photos[0], caption=post.caption)
