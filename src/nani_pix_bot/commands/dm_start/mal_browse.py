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
into whichever follow-up step the game is due — the screenshot-source
picker for a screenshot-less /newgame, or straight to the confirmation
preview if they'd already uploaded an image — exactly as an
AniList/Shikimori/Tenrai/TMDB search pick does. A MAL-list pick is
identification-by-catalog and nothing more; it never brings a photo of
its own.

Every network call below happens with no DB session open, per this
package's established convention (see _shared.py's
_stage_preview/_post_preview_album split for the same reasoning applied
to Telegram sends)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from loguru import logger
from telegram import InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SEARCH_SERVICE_ERRORS,
    _client_for_source,
    _method_keyboard,
    _post_preview_album,
    _reject_stale_tap,
    _reply_service_down,
    _reply_service_unavailable,
    _stage_preview,
)
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
from nani_pix_bot.services.search.tenrai import TenraiResult

# How many list entries one browser page shows. Ten keeps the keyboard
# short enough to read on a phone without scrolling past it, the same
# order of magnitude as the screenshot gallery's own page size.
MAL_LIST_PAGE_SIZE = 10

# What to call MyAnimeList when telling a player it's unreachable.
# A bare literal rather than a `Provider.display_name` because "MAL
# list" deliberately isn't a `Provider` (see this module's docstring),
# and an untranslated one because third-party brand names are never
# translated in this project — see CLAUDE.md's i18n section.
MAL_SERVICE_NAME = "MyAnimeList"


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
    linking again": they never linked, the refresh token itself is
    expired/revoked, or their stored tokens no longer decrypt. None of
    the three is an error worth surfacing as one — see the spec."""
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            credentials = mal_link.get_credentials(
                session,
                telegram_user_id,
                encryption_key=context.bot_data["mal_token_encryption_key"],
            )
    except InvalidToken:
        # MAL_TOKEN_ENCRYPTION_KEY was rotated (or the row predates the
        # current one), so these tokens are unrecoverable ciphertext.
        # get_credentials deliberately lets this propagate rather than
        # returning garbage; the spec says the *caller* turns it into
        # "unlinked, please link again" — which is exactly this, and
        # which is why the branch lives here and not in the service.
        logger.warning(
            "Player {}: stored MAL tokens no longer decrypt with the configured "
            "encryption key — treating them as unlinked",
            telegram_user_id,
        )
        return None
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


@dataclass(frozen=True)
class _ListPageRequest:
    """Who wants which page of their MAL list, and how to answer them.

    One object rather than four loose parameters threaded through both
    functions below — the same remedy (and for the same `qlty smells`
    many-parameters finding) as services/mal_link.py's `CredentialsData`
    and keyboards.py's `MalListPage`: fix the code, never the threshold.

    `send` is whichever reply callable the call site actually holds —
    `query.edit_message_text` for the two button taps, and
    `message.reply_text` for the pasted-code path, which has no callback
    query to edit."""

    send: Callable[..., Awaitable[object]]
    user: User
    offset: int
    lang: str


async def _fetch_mal_list_page(
    context: ContextTypes.DEFAULT_TYPE, request: _ListPageRequest
) -> tuple[str, InlineKeyboardMarkup] | None:
    """One page of the player's own MAL list, rendered as
    (caption, keyboard) — or None if they can't be authenticated at all
    (never linked, a refresh token MAL has since revoked, or tokens that
    no longer decrypt). Builds nothing but the message; `fetch_list`'s
    own failures are left to propagate to `_show_mal_list_page`, which
    owns the one reply every call site shares for them."""
    offset = request.offset
    access_token = await _current_access_token(context, request.user.id)
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
        request.user.id,
        offset,
        len(list_page.entries),
    )
    if not list_page.entries:
        # An empty list has no entries to build buttons from, so
        # mal_list_keyboard would render an empty InlineKeyboardMarkup —
        # and the caption tells the player to "pick another
        # identification method" with no method picker in sight. Hand
        # back the picker it names, the same way _reply_service_unavailable
        # does on the failure path.
        logger.info("Player {}'s MAL list came back empty at offset {}", request.user.id, offset)
        return i18n.t("dm_start.mal_list_empty", request.lang), _method_keyboard(
            context, request.lang
        )

    keyboard_page = MalListPage(
        offset=offset,
        count=len(list_page.entries),
        has_more=list_page.has_more,
        previous_offset=max(0, offset - MAL_LIST_PAGE_SIZE) if offset > 0 else None,
        entries=[(entry.mal_id, entry.title, entry.status) for entry in list_page.entries],
    )
    return i18n.t("dm_start.mal_list_prompt", request.lang), mal_list_keyboard(
        keyboard_page, request.lang
    )


async def _show_mal_list_page(
    context: ContextTypes.DEFAULT_TYPE, request: _ListPageRequest
) -> bool:
    """Fetch one page of the player's list and send it through
    `request.send`.

    False means one specific thing — the player has no usable MAL link —
    because that is the only outcome the three call sites answer
    differently (start linking / say so / fall back to the plain "you're
    linked" confirmation). True means the player has already been
    replied to and the caller is done, whether that reply was the list
    itself or MAL being unreachable.

    MAL's API failing is handled here rather than at each call site both
    to keep that reply identical across all three and because
    `mal_user.fetch_list` raises on any non-2xx by design (see its
    docstring) — including a 401 for an access token MAL revoked
    server-side before `expires_at` lapsed, which no amount of
    refresh-on-expiry can pre-empt. Unhandled, that left the starter
    staring at a dead spinner."""
    try:
        page = await _fetch_mal_list_page(context, request)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception(
            "MAL list fetch failed for player {} at offset {}", request.user.id, request.offset
        )
        # Not _reply_service_down: that one is Provider-typed and would
        # have to blame Tenrai for MyAnimeList's outage.
        await _reply_service_unavailable(request.send, request.lang, MAL_SERVICE_NAME, context)
        return True

    if page is None:
        return False
    caption, keyboard = page
    await request.send(caption, reply_markup=keyboard)
    return True


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
    the same walk-through (the spec's rule), and `_show_mal_list_page`
    already collapses the two into one False."""
    request = _ListPageRequest(send=query.edit_message_text, user=user, offset=0, lang=lang)
    if not await _show_mal_list_page(context, request):
        await _start_linking(query.edit_message_text, context, lang, user)


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

    # A False here can only mean the credentials written moments ago
    # vanished (a /unlinkmal racing this), so the plain confirmation
    # below is the right fallback; MAL being unreachable already got its
    # own reply inside the helper.
    request = _ListPageRequest(send=message.reply_text, user=user, offset=0, lang=lang)
    if should_open_browser and await _show_mal_list_page(context, request):
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

    request = _ListPageRequest(send=query.edit_message_text, user=user, offset=offset, lang=lang)
    if not await _show_mal_list_page(context, request):
        logger.warning("Player {} paged a MAL list they're no longer linked to", user.id)
        await query.edit_message_text(i18n.t("mal_link.not_linked_anymore", lang))


async def mal_list_pick_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A specific anime tapped in the list browser. A MAL id IS a Tenrai
    id (same catalog), so this resolves through the existing Tenrai
    pipeline and then hands off to `game_service.stage_result` and
    whichever follow-up step the game is actually due — the identical
    ending every other catalog identification method already has (see
    search.py's `pick_callback_handler`, whose two branches this
    mirrors)."""
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

    result = await _resolve_picked_mal_entry(query, context, mal_id, lang)
    if result is None:
        return

    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        album = None
        picker_prompt = None
        message_key = None
        if setup_game is not None:
            game_service.stage_result(setup_game, result, source=Provider.TENRAI)
            logger.info("Game {}: staged MAL list entry {}", setup_game.id, mal_id)
            if setup_game.original_image is not None:
                # Photo-first entry: the starter uploaded a screenshot and
                # only then identified it from their list, so sending them
                # to "pick a screenshot source" would sideline the image
                # they already gave us. Straight to the preview instead —
                # exactly pick_callback_handler's has_image branch.
                album = _stage_preview(session, setup_game, lang)
                message_key = "dm_start.preview_sent"
            else:
                # Screenshot-less /newgame entry — pick a screenshot next.
                picker_prompt = stage_screenshot_picker(setup_game)
                message_key = "dm_start.identification_staged"

    # Every send below happens after the block above committed — see
    # _post_preview_album's/send_screenshot_picker_prompt's docstrings
    # for why. `message_key` is None only on the stale-row path, where
    # nothing was staged at all.
    if message_key is None:
        await _reject_stale_tap(query, user.id, lang)
        return
    await query.answer()
    if album is not None:
        await _post_preview_album(context, album, lang)
    elif picker_prompt is not None:
        await send_screenshot_picker_prompt(context, picker_prompt, lang)
    await query.edit_message_text(i18n.t(message_key, lang))


async def _resolve_picked_mal_entry(
    query, context: ContextTypes.DEFAULT_TYPE, mal_id: int, lang: str
) -> TenraiResult | None:
    """Resolve a tapped list entry to its Tenrai result, or reply and
    return None for either already-handled failure (Tenrai unreachable,
    or the id gone from the catalogue). Mirrors search.py's
    `_resolve_picked_result`, including its `_SEARCH_SERVICE_ERRORS`
    guard — a MAL-list pick resolves through a genuine Tenrai API call,
    so it can fail exactly the same ways every other pick site can, and
    was leaving the starter on a dead spinner when it did.

    Acknowledging the tap is deliberately left to the caller on the
    success path, for the reason pick_callback_handler spells out: a
    query id can only be answered once, and `_reject_stale_tap` needs
    that one answer to deliver its alert."""
    client = _client_for_source(context, Provider.TENRAI)
    try:
        result = await tenrai.get_by_id(client, mal_id)
    except _SEARCH_SERVICE_ERRORS:
        logger.exception("Tenrai get_by_id failed for MAL list entry {}", mal_id)
        await query.answer()
        await _reply_service_down(query.edit_message_text, lang, Provider.TENRAI, context)
        return None

    if result is None:
        logger.warning("MAL list entry {} picked but the catalogue no longer has it", mal_id)
        await query.answer()
        # With a keyboard: this edit replaces the list keyboard, and the
        # text it replaces it with says "try another one from your list"
        # — which there would be no way back to. The method picker is
        # the one screen that leads everywhere, including back into the
        # list (same reasoning as _reply_service_unavailable's).
        await query.edit_message_text(
            i18n.t("dm_start.mal_not_found_anymore", lang),
            reply_markup=_method_keyboard(context, lang),
        )
        return None
    return result
