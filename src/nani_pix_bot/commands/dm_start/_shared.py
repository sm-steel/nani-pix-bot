"""Small helpers shared by more than one submodule of this package."""

import re
from collections.abc import Awaitable, Callable
from typing import TypeVar, assert_never, cast

import httpx
from loguru import logger
from telegram import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start.keyboards import (
    method_selection_keyboard,
    preview_keyboard,
    screenshot_source_keyboard,
)
from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config

# "The provider is unreachable" in all the shapes it actually arrives in.
#
# httpx.HTTPError/RuntimeError are what services/search/ raises on a
# network failure, exhausted rate-limit retries, or a body that came back
# 200 but wasn't the JSON we asked for (a throttle page served as HTML,
# say) — see rest.py's get_json()/http_retry(), which is where that
# plumbing lives since the REST refactor deleted the per-provider
# _request() helpers this comment used to point at.
#
# TelegramError is here because a Telegram failure can mean the same
# thing: the gallery hands provider URLs to Telegram to fetch
# server-side, so a hotlink block, an over-10MB frame or a dead CDN path
# comes back as a telegram.error.BadRequest rather than as an httpx error
# of our own. Note where that is actually caught, though — it is
# screenshots.py's _show_gallery_page, and it catches `except
# TelegramError` directly rather than this tuple. Deliberately: the only
# statements in that try are the two sends, and widening it to this tuple
# would put RuntimeError around the keyboard/i18n construction evaluated
# inside the call, which is the "report a bug as a provider outage"
# pattern _IMAGE_DOWNLOAD_ERRORS below exists to avoid.
#
# So the member is defensive, not load-bearing: no current `except
# _SEARCH_SERVICE_ERRORS` site can raise one, since all six wrap provider
# HTTP calls only. Keep it that way. Moving a Telegram call inside one of
# those blocks would silently relabel its failure "the provider is down"
# — give such a call its own `except TelegramError` instead, the way
# _show_gallery_page does.
_SEARCH_SERVICE_ERRORS = (httpx.HTTPError, RuntimeError, TelegramError)

# Deliberately narrower than the tuple above, for the one call that is a
# plain httpx GET against a provider's CDN and nothing else:
# screenshot_gallery.py's download of the picked screenshot's bytes.
# Catching RuntimeError there would take any ordinary bug in that code
# path, swallow its traceback and report it to the starter as "the
# provider is down" — sending them round the source menu for a provider
# that is working fine.
_IMAGE_DOWNLOAD_ERRORS = (httpx.HTTPError,)

# Used by both manual.py's second-message step and preview.py's
# "add a synonym" step.
_SYNONYM_SPLIT_RE = re.compile(r"[,\n]")

# TMDB is the only provider needing its own client (DNS-blocked direct
# from moscow, needs a proxy + Bearer-token auth — see app.py's
# build_application()); every other provider shares "search_client".
# Used by search.py's search/pick flow and both screenshots.py's/
# screenshot_gallery.py's gallery flow.
_TMDB_CLIENT_BOT_DATA_KEY = "tmdb_client"

_ResultT = TypeVar("_ResultT")


async def _search_and_build_keyboard(
    client: httpx.AsyncClient,
    query: str,
    search_fn: Callable[[httpx.AsyncClient, str], Awaitable[list[_ResultT]]],
    keyboard_fn: Callable[[list[_ResultT]], InlineKeyboardMarkup],
) -> tuple[list[_ResultT], InlineKeyboardMarkup | None]:
    """Search + build-the-results-keyboard, shared by search.py's
    `_search_step` and screenshot_gallery.py's `_screenshot_search_step`
    — the two call sites that pair a provider search with a
    provider-specific `*_results_keyboard` builder, and so are exactly
    where the concrete result type matters (unlike
    search.py's own `_get_identification_result`, which never needs a
    keyboard and collapses straight into a `Provider`-keyed dict
    instead). A `TypeVar`-generic helper is how `rest.py`'s
    `fetch_by_id` and `keyboards.py`'s `_results_keyboard` already solve
    the same "one provider's concrete type has to survive the call"
    problem — reused here rather than introducing `Protocol`/`@overload`
    as a first use of either in this codebase.

    `keyboard_fn` is a one-argument callable on purpose: each call site
    passes a lambda that already closes over `lang` (and, for the
    screenshot variant, `pick_prefix`), the same way `keyboards.py`'s own
    `_ResultAccessors` instantiations bind their per-provider closures
    rather than widening a shared function's parameter list. This helper
    never needs to know either exists.

    Returns `(results, keyboard)`, not just the keyboard: both callers
    log `len(results)` in their own debug line right after this returns,
    and handing `results` back means they keep doing that rather than
    recomputing or losing it. `keyboard` is None for an empty result
    list — the caller's own empty-results branch decides what to say
    about that.

    Deliberately doesn't catch anything: the two call sites' failure
    handling genuinely differs (different fallback screens), so each
    keeps its own `try/except _SEARCH_SERVICE_ERRORS` wrapped around a
    call to this helper, unchanged in shape from before."""
    results = await search_fn(client, query)
    keyboard = keyboard_fn(results) if results else None
    return results, keyboard


def _client_for_source(context: ContextTypes.DEFAULT_TYPE, source: Provider) -> httpx.AsyncClient:
    """The httpx client to talk to `source` with. Annotated on both ends
    on purpose: `bot_data` is an untyped dict, so an unannotated return
    made this Unknown — and since every search, screenshot fetch and
    image download in the package funnels through here, that one Unknown
    was enough to stop `ty` checking a single call made on a client
    anywhere downstream. app.py puts a real AsyncClient under both keys
    (see build_application), so the cast states what is already true
    rather than papering over a doubt."""
    key = _TMDB_CLIENT_BOT_DATA_KEY if source == Provider.TMDB else "search_client"
    return cast(httpx.AsyncClient, context.bot_data[key])


async def _reject_stale_tap(query: CallbackQuery, user_id: int, lang: str) -> None:
    """The one thing every screen of the screenshot sub-flow has to say
    when a button is tapped and there is no SETUP game left to act on:
    the row was resolved (confirmed, stopped) or the setup-abandon timer
    deleted it an hour in, while the messages it left behind stayed
    tappable forever. It is a rejected action, so WARNING, per CLAUDE.md's
    table — and an alert on screen, so the starter tapping a dead
    keyboard is told why nothing happens (issue #79).

    An **alert**, not a plain toast: the screen these sites answer for is
    finished, so the message has to survive being read. The two things it
    says are the two things that are true of every one of them — the
    round is gone, and here is how to start another. Not
    `dm_start.setup_abandoned`, the nearest existing string: that one is
    worded as a group announcement and asserts the turn is open to
    anyone, which is false when the row went away via /stop or because
    the game actually started.

    **The alert is the handler's only `query.answer` on this path.**
    Telegram invalidates a callback query id the moment it is answered,
    so a bare acknowledgement above the lookup would spend the answer
    these sites need and leave the text with nowhere to go. Every caller
    therefore acknowledges a *successful* tap below this branch instead
    — see each handler for where its own single answer sits.

    One helper rather than the same lines at each of the six sites, so
    the wording production greps for can't drift between them —
    `opt(depth=1)` so loguru still stamps the *caller's* frame rather
    than this one. Without it every site logged the same
    `_shared:_reject_stale_tap:<the logger.warning below>` — one fixed
    location, whatever line it currently sits on — and two of them (the
    pair inside screenshot_search_pick_callback_handler, which also
    share a callback prefix) became byte-identical, erasing the only
    thing that told a mundane hour-old tap apart from a row that
    vanished mid-round-trip. No literal line number here on purpose:
    the last one went stale twice over, and it was a comment about line
    numbers that did it."""
    logger.opt(depth=1).warning(
        "Starter {} tapped {!r} with no SETUP game left — already resolved, or the "
        "setup-abandon timer deleted the row",
        user_id,
        query.data,
    )
    await query.answer(i18n.t("dm_start.setup_gone", lang), show_alert=True)


def _prefer_shikimori(lang: str) -> bool:
    return lang.upper() == "RU"


def _method_prompt_key(*, prefer_shikimori: bool) -> str:
    return (
        "dm_start.pick_method_prompt_shikimori_preferred"
        if prefer_shikimori
        else "dm_start.pick_method_prompt"
    )


def _stored_provider(stored: str) -> Provider:
    """A provider value read back off one of `Game`'s three
    `Provider`-typed columns, as a real `Provider` member.

    Those columns are deliberately `String`-backed (see models/game.py),
    which means SQLAlchemy has no idea they are enum-shaped: `ty` reads
    the `Mapped[Provider...]` annotation and sees a `Provider`, but at
    runtime a plain `str` comes back. Everything `Provider` inherits from
    `str` works on it regardless — `==` in both directions, `in` against
    a list of members, even a dict keyed by members (a StrEnum hashes as
    its value) — so the *only* thing that breaks is member-specific
    attribute access, and `.display_name` is exactly that. It raised
    `AttributeError: 'str' object has no attribute 'display_name'` from
    three failure screens, i.e. only when a provider was already down.

    So conversion happens once, here, at each of the four sites that
    read one of those columns into a `Provider`-typed slot — rather than
    defensively at every `.display_name` — and `ty` is right about
    everything downstream of it. Those four: `search.py`'s
    `search_text_handler` (twice — the picker column and `source`),
    `screenshots.py`'s `resume_screenshot_gallery`, and
    `_screenshot_capable_providers` just below.

    That last one is the load-bearing one, and the reason "only failure
    screens are affected" understates this. It puts its result in a
    `list[Provider]` that the source menu is drawn from, so without the
    conversion a bare `str` reaches `_source_label`'s `.display_name` on
    the ordinary screenshot-source screen — the happy path, not a
    failure path."""
    return Provider(stored)


def _screenshot_capable_providers(game: Game) -> list[Provider]:
    """All 3 screenshot-capable providers, same-provider-as-identification
    first when it's one of them (so the common case — screenshot source
    matches identification source — needs no cross-provider search at
    all). Every provider is offered regardless of whether the game
    already has an id for it — tapping one it doesn't triggers
    cross-provider resolution (see screenshots.py's
    _resolve_screenshot_source).

    Lives here, not in screenshots.py where its callers are, because
    `_current_setup_screen` below needs it too and screenshots.py
    already imports this module — the other direction would be a cycle.
    It is pure `game.source` arithmetic either way, with no dependency
    on the screenshot sub-flow around it."""
    candidates = [Provider.SHIKIMORI, Provider.JIKAN, Provider.TMDB]
    # `game.source` arrives as a bare str (see `_stored_provider`), and
    # can legitimately be "manual" — which is in neither list, so the
    # membership test settles both questions at once and nothing below it
    # ever converts a non-provider.
    if game.source in candidates:
        identified_by = _stored_provider(game.source)
        candidates.remove(identified_by)
        candidates.insert(0, identified_by)
    return candidates


def _current_setup_screen(game: Game, lang: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """The screen `game`'s own starter is already looking at, as the i18n
    key and keyboard to re-send it with — see `_resume_setup`.

    Every step is covered, and each returns the same prompt/keyboard
    pair the step's own handler sends, so re-showing one can never
    invent a screen the flow doesn't otherwise have. The two
    input-prompt steps have no keyboard in the flow either: their route
    forward is the photo or text being asked for, which the prompt
    itself states.

    What comes back is the step's *entry* screen, which for the two
    steps that span several is not necessarily the exact sub-screen the
    starter last saw: mid-search at PICKING_METHOD gets the method menu
    rather than the "type a title" prompt, and mid-gallery at
    PICKING_SCREENSHOT gets the source menu rather than that gallery
    page. Both are one step back within the same step — which is what
    "I'm stuck" is asking for anyway — and both are screens the flow
    already produces there.

    The final branch is spelled out rather than left as a fall-through,
    with `assert_never` behind it: a sixth SetupStep would otherwise be
    handed the synonym prompt in silence. It fails at type-check time
    now, and loudly at runtime if one is ever added dynamically."""
    if game.setup_step == SetupStep.PICKING_METHOD:
        prefer_shikimori = _prefer_shikimori(lang)
        return (
            _method_prompt_key(prefer_shikimori=prefer_shikimori),
            method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
        )
    if game.setup_step == SetupStep.PICKING_SCREENSHOT:
        return (
            "dm_start.pick_screenshot_source_prompt",
            screenshot_source_keyboard(_screenshot_capable_providers(game), lang),
        )
    if game.setup_step == SetupStep.CONFIRMING:
        return "dm_start.preview_confirm_prompt", preview_keyboard(lang)
    if game.setup_step == SetupStep.AWAITING_PHOTO_CHANGE:
        return "dm_start.ask_new_photo", None
    if game.setup_step == SetupStep.AWAITING_SYNONYM:
        return "dm_start.ask_extra_synonym", None
    assert_never(game.setup_step)


async def _resume_setup(message, game: Game, lang: str) -> None:
    """ "Start a new game" from someone whose own setup is still open —
    most often "I'm stuck, let me start over", which is exactly the
    population the "never strand the starter" work was for.

    `can_start()` answers False for them, because it is False whenever
    *any* SETUP game exists, and the reply it drives (`not_your_turn`)
    is then untrue in the one way that helps least: it is their turn,
    they are mid-way through taking it. So put them back on the step
    they are actually on instead — on that step's entry screen, which
    is not always the exact sub-screen they last saw (see
    `_current_setup_screen`). Nothing is written — no status, no
    step, no picker column (#69's invariant is about screen
    *transitions*; this re-sends a screen the game is already on) — and
    the row keeps the setup-abandon timer it was created with, so
    nothing is rescheduled either.

    Genuinely starting over is still `/stop`, unchanged; this only stops
    the bot from denying the setup exists — and says so, on the two steps
    where the screen alone cannot (issue #79, routed from #73)."""
    key, keyboard = _current_setup_screen(game, lang)
    text = i18n.t(key, lang)
    if keyboard is None:
        # AWAITING_PHOTO_CHANGE and AWAITING_SYNONYM: the two steps whose
        # route forward is the photo or text being asked for, so there is
        # no keyboard to make this recognisable *as* a re-show. On its
        # own the prompt reads as a fresh question and answers the
        # /newgame with nothing, which is the same "the bot is ignoring
        # me" this function exists to end — so the one thing the screen
        # can't say (your setup is still open, and /stop abandons it)
        # goes above it.
        text = f"{i18n.t('dm_start.setup_already_open', lang)}\n\n{text}"
    logger.warning(
        "Starter {} asked for a new game while their own setup (game {}) is still at {} — "
        "re-showing that step instead of refusing them the turn",
        game.starter_id,
        game.id,
        game.setup_step.value,
    )
    await message.reply_text(text, reply_markup=keyboard)


async def _reply_service_down(send, lang: str, source: Provider) -> None:
    """Shared failure path for both the search step and the pick step:
    tell the starter the chosen service looks unreachable and hand them
    back the method-selection keyboard rather than leaving them stuck
    with a dead-end SETUP game (see issue #11's orphaned-row incident)."""
    prefer_shikimori = _prefer_shikimori(lang)
    await send(
        i18n.t("dm_start.search_failed", lang, service=source.display_name),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )


async def _start_new_game(
    message, context, session_factory, user, image_bytes: bytes | None = None
) -> None:
    """Creates a new SETUP game and shows the method-selection keyboard —
    shared by both entry points into the setup flow: `intake.py`'s
    `photo_handler` (real image bytes already in hand) and `newgame.py`'s
    `/newgame` command (no image yet — filled in later, either by
    picking a screenshot or by falling back to an upload).

    Unless the caller already has a setup of their own open, in which
    case it re-shows that game's current step and creates nothing (see
    `_resume_setup`). Both entry points get that for free by sharing
    this helper: a second `/newgame`, and a photo sent on a step that
    doesn't take one (intake.py handles the two that do), are the same
    "my setup is already open" mistake arriving by different routes."""
    group_chat_id = context.bot_data["group_chat_id"]
    if not await is_group_member(context.bot, group_chat_id, user.id):
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        logger.warning("Non-member {} tried to start a game via DM", user.id)
        await message.reply_text(i18n.t("dm_start.not_a_member", lang))
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if not settings.get_games_enabled(session):
            logger.warning("{} tried to start a game while games are disabled", user.id)
            await message.reply_text(i18n.t("dm_start.games_disabled", lang))
            return
        players.get_or_create_player(session, user.id, username=user.username)
        # Whose SETUP game is in the way has to be settled *before*
        # can_start(), which can't tell: it refuses on any SETUP game at
        # all, the caller's own included (see _resume_setup).
        own_setup = game_service.get_setup_game_for_starter(session, user.id)
        if own_setup is not None:
            await _resume_setup(message, own_setup, lang)
            return
        if not game_service.can_start(session, user.id):
            logger.warning("{} tried to start a game out of turn", user.id)
            await message.reply_text(i18n.t("dm_start.not_your_turn", lang))
            return
        new_game = game_service.create_setup_game(
            session, starter_id=user.id, original_image=image_bytes
        )
        # They're clearly not missing their turn if they've already
        # started it — the setup-abandon timer takes over from here.
        game_service.clear_turn_timers(session)

    timeout_module.cancel_turn_timers(context.job_queue)
    timeout_module.schedule_setup_abandon(context.job_queue, new_game)
    await context.bot.send_message(
        chat_id=group_chat_id,
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("dm_start.setup_started_group_notice", lang, starter=user.full_name),
    )

    prefer_shikimori = _prefer_shikimori(lang)
    await message.reply_text(
        i18n.t(_method_prompt_key(prefer_shikimori=prefer_shikimori), lang),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )


async def _show_preview(context, session, game: Game, lang: str) -> None:
    """Send the starter a private preview of every pixelation stage for
    the staged title/synonyms — one Telegram album (blockiest to
    clearest, see MECHANICS.md) — so they see how the round will
    actually look before committing to it. `sendMediaGroup` has no
    `reply_markup` support, so the confirm/change-image/research/add-
    synonym buttons go on a short separate follow-up text message right
    after the album, not on the album itself. Nothing is posted to the
    group until "Confirm and start" is tapped. Always the game's own
    starter's DM — every caller looked this game up by starter_id in the
    first place, so game.starter_id is the right chat_id.

    Shared by every path that ends in "an image now exists for this
    game" — the traditional upload flow (intake.py, manual.py,
    preview.py's own add-synonym step), and the screenshot-picker flow
    (screenshot_gallery.py's pick), hence living here rather than in
    preview.py."""
    if game.original_image is None:
        raise AssertionError("game.original_image is None in _show_preview")
    original_bytes = game.original_image
    config = stage_config.get_stage_configs(session)
    title = game_service.display_title(game, lang)
    # Every stored title variant is already an accepted /guess — not just
    # the manually-typed synonyms — so show all of them here too, minus
    # whatever's already shown as the Title above. match_candidates() is
    # the same list record_guess() actually matches against, so this can
    # never drift from what's really accepted (see issue #50).
    other_answers = [
        candidate
        for candidate in dict.fromkeys(game_service.match_candidates(game))
        if candidate != title
    ]
    answers = ", ".join(other_answers) or "—"
    caption = i18n.t("dm_start.preview_caption", lang, title=title, answers=answers)
    captions = [caption, *([None] * (len(game_service.STAGE_ORDER) - 1))]
    media = [
        InputMediaPhoto(
            media=pixelate_service.pixelate(original_bytes, config[stage].target_width),
            caption=stage_caption,
        )
        for stage, stage_caption in zip(game_service.STAGE_ORDER, captions, strict=True)
    ]
    game.setup_step = SetupStep.CONFIRMING
    logger.debug("Game {}: showing {}-stage confirmation preview album", game.id, len(media))
    await context.bot.send_media_group(chat_id=game.starter_id, media=media)
    await context.bot.send_message(
        chat_id=game.starter_id,
        text=i18n.t("dm_start.preview_confirm_prompt", lang),
        reply_markup=preview_keyboard(lang),
    )
