"""Everything triggered by the "My MAL List" identification method: the
6th method-picker button (unlinked -> starts linking, linked -> opens
the browser), the pasted-back authorization code, and the paginated
list browser's own callback handlers. See
commands/dm_start/search.py for how this plugs into
`method_pick_callback_handler`/`search_text_handler`, and
docs/superpowers/specs/2026-09-21-mal-account-linking-design.md for the
full design.

"MAL list" is NOT a `Provider` (see services/search/mal_user.py's module
docstring) — nothing here ever writes "mal_list" into `Game.source`.
What a pick *does* write is `Provider.TENRAI`: a MAL id and a Tenrai id
are the same id (same catalog), so resolving a picked entry goes
through the existing `tenrai.get_by_id` and lands in
`game_service.stage_result`, the one shared landing spot every other
identification method already ends in. From there the player continues
into the ordinary screenshot-source picker — a MAL-list pick is
identification-by-catalog, exactly the same shape as an
AniList/Shikimori/Tenrai/TMDB search pick, not a "photo already in
hand" entry.

Every network call below happens with no DB session open, per this
package's established convention (see _shared.py's
_stage_preview/_post_preview_album split for the same reasoning applied
to Telegram sends)."""

from loguru import logger
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _client_for_source, _reject_stale_tap
from nani_pix_bot.commands.dm_start.keyboards import (
    MalListPage,
    mal_list_keyboard,
    parse_mal_list_page_callback_data,
    parse_mal_list_pick_callback_data,
)
from nani_pix_bot.commands.dm_start.screenshots import (
    send_screenshot_picker_prompt,
    stage_screenshot_picker,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import seconds_until
from nani_pix_bot.jobs.timers.mal_link_expiry import schedule_mal_link_expiry
from nani_pix_bot.models.enums import Provider, SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, mal_link, settings
from nani_pix_bot.services.search import mal_user, tenrai

# How many list entries one browser page shows. Ten keeps the keyboard
# short enough to read on a phone without scrolling past it, the same
# order of magnitude as the screenshot gallery's own page size.
MAL_LIST_PAGE_SIZE = 10


def _oauth_app(context: ContextTypes.DEFAULT_TYPE) -> mal_user.MalOAuthApp:
    """This bot's own MAL developer-app identity, assembled from the
    `bot_data` keys app.py fills in — see mal_user.MalOAuthApp."""
    return mal_user.MalOAuthApp(
        client_id=context.bot_data["mal_client_id"],
        client_secret=context.bot_data["mal_client_secret"],
        redirect_uri=context.bot_data["mal_redirect_uri"],
    )


def _store_tokens(
    context: ContextTypes.DEFAULT_TYPE,
    telegram_user_id: int,
    tokens: mal_user.MalTokenResponse,
    *,
    mal_username: str | None,
) -> None:
    """Persist a freshly issued token pair (from either the initial code
    exchange or a refresh) in its own short session — deliberately not
    folded into whichever session the caller already had open, since the
    network call that produced `tokens` must not span one."""
    with session_scope(context.bot_data["session_factory"]) as session:
        mal_link.upsert_credentials(
            session,
            telegram_user_id,
            data=mal_link.CredentialsData(
                encryption_key=context.bot_data["mal_token_encryption_key"],
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
                mal_username=mal_username,
            ),
        )


async def _current_access_token(
    context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int
) -> str | None:
    """A usable access token for `telegram_user_id`, refreshing on demand
    when the stored one has already expired (the spec's "refresh when
    about to fetch a list" rule — MAL access tokens are short-lived and
    nothing else in this bot renews them).

    None means "treat this player as unlinked and walk them through
    linking again": either they never linked, or the refresh token
    itself is expired/revoked, which is not an error worth surfacing as
    one — see the spec."""
    with session_scope(context.bot_data["session_factory"]) as session:
        credentials = mal_link.get_credentials(
            session, telegram_user_id, encryption_key=context.bot_data["mal_token_encryption_key"]
        )
    if credentials is None:
        return None
    # `seconds_until` also normalizes the naive datetime a DATETIME column
    # round-trips as — see its docstring.
    if seconds_until(credentials.expires_at) > 0:
        return credentials.access_token

    logger.info("Player {}: MAL access token expired, refreshing", telegram_user_id)
    tokens = await mal_user.refresh_tokens(
        context.bot_data["mal_client"],
        _oauth_app(context),
        refresh_token=credentials.refresh_token,
    )
    if tokens is None:
        logger.warning(
            "Player {}: MAL refused the refresh token — treating them as unlinked",
            telegram_user_id,
        )
        return None
    _store_tokens(context, telegram_user_id, tokens, mal_username=credentials.mal_username)
    return tokens.access_token


async def _fetch_mal_list_page(
    context: ContextTypes.DEFAULT_TYPE, user, *, offset: int, lang: str
) -> tuple[str, InlineKeyboardMarkup] | None:
    """One page of the player's own MAL list, rendered as
    (caption, keyboard) — or None if they can't be authenticated at all
    (never linked, or a refresh token MAL has since revoked). Builds
    nothing but the message: each of the three call sites does its own
    send with whatever object (`query`/`message`) it actually holds."""
    access_token = await _current_access_token(context, user.id)
    if access_token is None:
        return None

    list_page = await mal_user.fetch_list(
        context.bot_data["mal_client"],
        access_token,
        offset=offset,
        limit=MAL_LIST_PAGE_SIZE,
    )
    logger.debug(
        "Player {}: MAL list page at offset {} returned {} entries",
        user.id,
        offset,
        len(list_page.entries),
    )
    keyboard_page = MalListPage(
        offset=offset,
        count=len(list_page.entries),
        has_more=list_page.has_more,
        previous_offset=max(0, offset - MAL_LIST_PAGE_SIZE) if offset > 0 else None,
        entries=[(entry.mal_id, entry.title, entry.status) for entry in list_page.entries],
    )
    caption_key = "dm_start.mal_list_prompt" if list_page.entries else "dm_start.mal_list_empty"
    return i18n.t(caption_key, lang), mal_list_keyboard(keyboard_page, lang)


async def _start_linking(send, context: ContextTypes.DEFAULT_TYPE, lang: str, user) -> None:
    """The same linking walk-through /linkmal does (commands/mal_link.py),
    reached instead from the method picker's 6th button when the player
    turns out not to have a usable link. `send` is whatever reply
    callable the call site holds (`message.reply_text` /
    `query.edit_message_text`).

    Its own prompt, not `mal_link.authorize_prompt`: this one was
    triggered mid-/newgame, so it can promise what happens next (the
    list opens straight away — see `_handle_mal_code_paste`'s
    `should_open_browser`), which the standalone command can't."""
    state, code_verifier = mal_user.generate_state_and_verifier()
    with session_scope(context.bot_data["session_factory"]) as session:
        mal_link.upsert_pending_link(session, user.id, state=state, code_verifier=code_verifier)
        authorize_url = mal_user.build_authorize_url(
            client_id=context.bot_data["mal_client_id"],
            redirect_uri=context.bot_data["mal_redirect_uri"],
            state=state,
            code_verifier=code_verifier,
        )

    schedule_mal_link_expiry(context.job_queue, user.id)
    logger.info("Player {} started MAL linking from the method picker", user.id)
    await send(i18n.t("mal_link.authorize_prompt_for_list", lang, url=authorize_url))


async def handle_mal_method_tap(query, context: ContextTypes.DEFAULT_TYPE, lang: str, user) -> None:
    """Called from `method_pick_callback_handler` when the tapped button
    is the MAL-list one — diverts BEFORE that function's generic "store
    the source on the game row + prompt for search text" logic, since
    "mal_list" is not a `Provider` and there is no free-text search step
    at all here.

    Tries to open the browser first and falls back to linking, rather
    than branching on a separate `get_credentials` probe: "linked" and
    "linked but MAL has revoked the refresh token" both have to end in
    the same walk-through (the spec's rule), and `_fetch_mal_list_page`
    already collapses the two into one None."""
    page = await _fetch_mal_list_page(context, user, offset=0, lang=lang)
    if page is None:
        await _start_linking(query.edit_message_text, context, lang, user)
        return
    caption, keyboard = page
    await query.edit_message_text(caption, reply_markup=keyboard)


async def _handle_mal_code_paste(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, user
) -> None:
    """The player's next plain-text DM after /linkmal (or the 6th button)
    is treated as the pasted authorization code — see
    `search_text_handler`'s early-exit branch for the check that routes
    it here."""
    session_factory = context.bot_data["session_factory"]

    with session_scope(session_factory) as session:
        pending = mal_link.get_pending_link(session, user.id)
        if pending is None:
            return  # shouldn't happen — search_text_handler already checked
        code_verifier = pending.code_verifier

    tokens = await mal_user.exchange_code_for_tokens(
        context.bot_data["mal_client"],
        _oauth_app(context),
        code=message.text.strip(),
        code_verifier=code_verifier,
    )
    if tokens is None:
        # The pending row deliberately stays: a mistyped or stale paste
        # should be retryable without another /linkmal round-trip.
        logger.warning("Player {}: MAL rejected the pasted authorization code", user.id)
        await message.reply_text(i18n.t("mal_link.code_rejected", lang))
        return

    _store_tokens(context, user.id, tokens, mal_username=None)
    with session_scope(session_factory) as session:
        mal_link.delete_pending_link(session, user.id)
        # A player who's mid-/newgame setup and still picking an
        # identification method gets dropped straight into their list
        # browser once linking succeeds — reaching their list was the
        # entire point of a button-triggered link (see the spec's
        # confirmed decision). A standalone /linkmal (no active setup
        # game, or one already past the method-pick step) just gets the
        # plain confirmation instead.
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        should_open_browser = (
            setup_game is not None and setup_game.setup_step == SetupStep.PICKING_METHOD
        )
    logger.info("Player {} finished linking their MAL account", user.id)

    if should_open_browser:
        page = await _fetch_mal_list_page(context, user, offset=0, lang=lang)
        if page is not None:
            caption, keyboard = page
            await message.reply_text(caption, reply_markup=keyboard)
            return

    await message.reply_text(i18n.t("mal_link.linked", lang))


async def mal_list_page_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A Back/More tap in the list browser."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    offset = parse_mal_list_page_callback_data(query.data)
    user = query.from_user
    if offset is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    page = await _fetch_mal_list_page(context, user, offset=offset, lang=lang)
    if page is None:
        logger.warning("Player {} paged a MAL list they're no longer linked to", user.id)
        await query.edit_message_text(i18n.t("mal_link.not_linked_anymore", lang))
        return
    caption, keyboard = page
    await query.edit_message_text(caption, reply_markup=keyboard)


async def mal_list_pick_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A specific anime tapped in the list browser. A MAL id IS a Tenrai
    id (same catalog), so this resolves through the existing Tenrai
    pipeline and then hands off to `game_service.stage_result` +
    the shared screenshot-source picker — the identical ending every
    other catalog identification method already has (see
    search.py's `pick_callback_handler`)."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    mal_id = parse_mal_list_pick_callback_data(query.data)
    user = query.from_user
    if mal_id is None or user is None:
        await query.answer()
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    # Deliberately unacknowledged until below the stale-row check, for
    # the reason pick_callback_handler spells out: a query id can only be
    # answered once, and _reject_stale_tap needs that one answer to
    # deliver its alert.
    result = await tenrai.get_by_id(_client_for_source(context, Provider.TENRAI), mal_id)
    if result is None:
        logger.warning("MAL list entry {} picked but the catalogue no longer has it", mal_id)
        await query.answer()
        await query.edit_message_text(i18n.t("dm_start.mal_not_found_anymore", lang))
        return

    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            picker_prompt = None
        else:
            game_service.stage_result(setup_game, result, source=Provider.TENRAI)
            logger.info("Game {}: staged MAL list entry {}", setup_game.id, mal_id)
            picker_prompt = stage_screenshot_picker(setup_game)

    # Every send below happens after the block above committed — see
    # send_screenshot_picker_prompt's docstring for why.
    if picker_prompt is None:
        await _reject_stale_tap(query, user.id, lang)
        return
    await query.answer()
    await send_screenshot_picker_prompt(context, picker_prompt, lang)
    await query.edit_message_text(i18n.t("dm_start.identification_staged", lang))
