"""Adding to the author's clue screenshot list from a source (issue #452):
the clue panel's "Add from a source" lists Shikimori/TMDB (clue_panel.py),
and this pages through the chosen one's screenshots and adds a pick to the
list — never to the main screenshot, whose columns (`original_image`,
`screenshot_source`, `shown_screenshot_urls`) and setup step this leaves
alone. Reuses the setup gallery's fetch and album send (screenshots.py).

The usual order: read what's needed in one short session, fetch and
download with none open, then write and re-show the panel. Buttons carry
the screenshot's index in the source's own list, so a pick stays the same
screenshot however the shown page was filtered."""

from dataclasses import dataclass

from loguru import logger
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import clue_screens
from nani_pix_bot.commands.dm_start._shared import (
    IMAGE_DOWNLOAD_ERRORS,
    _reject_stale_tap_answered,
    client_for_source,
)
from nani_pix_bot.commands.dm_start.clue_keyboards import (
    CLUE_GALLERY_PICK_PREFIX,
    ClueGalleryPage,
    clue_back_keyboard,
    clue_gallery_keyboard,
    parse_clue_gallery,
)
from nani_pix_bot.commands.dm_start.screenshots import (
    GALLERY_PAGE_SIZE,
    GalleryTarget,
    ScreenshotFailure,
    _deliver_album,
    _fetch_screenshots_or_fallback,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import Provider, SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.clues import author_list, shop

# A fetch failure's message here: the setup gallery's own ones point at
# its source menu, which this gallery doesn't have.
_SERVICE_DOWN_KEY = "dm_start.screenshot_service_down"


@dataclass(frozen=True)
class _Source:
    """What the network step needs, read in one short session."""

    chat_id: int
    lang: str
    provider: Provider
    provider_id: int | None
    game_id: int | None  # None: no SETUP game this tap can act on
    excluded: frozenset[str] = frozenset()  # the main screenshot's and the list's URLs
    gone: bool = False  # no SETUP game at all, not just one that moved on


def _read_source(context: ContextTypes.DEFAULT_TYPE, user_id: int, provider: Provider) -> _Source:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user_id)
        if game is None:
            return _Source(user_id, lang, provider, None, None, gone=True)
        if not clue_screens.accepts_panel_taps(game) or provider not in (
            shop.clue_screenshot_providers(game)
        ):
            logger.warning(
                "tapped an old {provider} clue gallery (setup step {step}) — dropping its buttons",
                provider=provider,
                step=game.setup_step,
            )
            return _Source(user_id, lang, provider, None, None)
        listed = {entry.url for entry in author_list.entries(session, game.id) if entry.url}
        excluded = frozenset(game.shown_screenshot_urls or []) | listed
        provider_id = getattr(game, provider.id_attr_name)
        return _Source(user_id, lang, provider, provider_id, game.id, excluded)


async def clue_gallery_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A `cluegal:` button: a source's first page, a later page, or a pick.
    Answered up front, since the provider round-trip can outlast Telegram's
    callback window."""
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    await query.answer()
    parsed = parse_clue_gallery(query.data)  # a forged one is logged there
    if parsed is None:
        return
    kind, provider, number = parsed
    source = _read_source(context, query.from_user.id, provider)
    if source.game_id is None:
        if source.gone:
            await _reject_stale_tap_answered(context, source.chat_id, query.data, source.lang)
        await query.edit_message_reply_markup(reply_markup=None)
        return
    urls = await _fetch_screenshots_or_fallback(
        context, source.game_id, provider, source.provider_id
    )
    if isinstance(urls, ScreenshotFailure):
        await _show_failure(query, source, urls.key)
    elif kind == CLUE_GALLERY_PICK_PREFIX:
        await _pick(context, query, source, urls, number)
    else:
        await _show_page(context, query, source, urls, number)


async def _show_failure(query, source: _Source, key: str) -> None:
    """The source couldn't be reached (or has nothing): say so, with a way
    back to the panel. Nothing about the setup changes."""
    key = (
        "dm_start.clue_service_down" if key == _SERVICE_DOWN_KEY else "dm_start.clue_gallery_empty"
    )
    logger.info(
        "no {provider} clue gallery: {key!r} — back to the panel", provider=source.provider, key=key
    )
    await query.edit_message_text(
        text=i18n.t(key, source.lang, service=source.provider.display_name),
        reply_markup=clue_back_keyboard(source.lang),
    )


async def _show_page(
    context: ContextTypes.DEFAULT_TYPE, query, source: _Source, urls: list[str], offset: int
) -> None:
    """A page of the source's screenshots not already the main one or on
    the list, then its buttons in a message of their own (an album can't
    carry any)."""
    offered = [(index, url) for index, url in enumerate(urls) if url not in source.excluded]
    shown = offered[offset : offset + GALLERY_PAGE_SIZE]
    if not shown:
        await _show_failure(query, source, "dm_start.no_screenshots_available")
        return
    target = GalleryTarget(chat_id=source.chat_id, provider=source.provider, offset=offset)
    if not await _deliver_album(context, target, [url for _, url in shown]):
        await _show_failure(query, source, _SERVICE_DOWN_KEY)
        return
    page = ClueGalleryPage(
        source.provider,
        offset,
        tuple(index for index, _ in shown),
        has_more=len(offered) > offset + len(shown),
    )
    try:
        await context.bot.send_message(
            chat_id=source.chat_id,
            text=i18n.t("dm_start.clue_gallery_prompt", source.lang),
            reply_markup=clue_gallery_keyboard(page, source.lang),
        )
    except TelegramError:
        logger.exception(
            "couldn't send the {provider} clue gallery's buttons", provider=page.provider
        )
        await _show_failure(query, source, _SERVICE_DOWN_KEY)
        return
    logger.info(
        "showed {count} {provider} clue screenshot(s) from offset {offset} ({offered} on offer)",
        count=len(shown),
        provider=source.provider,
        offset=offset,
        offered=len(offered),
        game_id=source.game_id,
    )
    await query.edit_message_text(
        text=i18n.t(
            "dm_start.clue_gallery_shown", source.lang, service=source.provider.display_name
        )
    )


async def _download(context: ContextTypes.DEFAULT_TYPE, source: _Source, url: str) -> bytes | None:
    try:
        response = await client_for_source(context, source.provider).get(url)
        response.raise_for_status()
    except IMAGE_DOWNLOAD_ERRORS:
        logger.opt(exception=True).warning(
            "couldn't download {provider} clue screenshot {url}", provider=source.provider, url=url
        )
        return None
    return response.content


async def _pick(
    context: ContextTypes.DEFAULT_TYPE, query, source: _Source, urls: list[str], index: int
) -> None:
    """Download the picked screenshot and add it to the list, then show the
    panel with how that went."""
    if not 0 <= index < len(urls):
        logger.warning(
            "picked {provider} clue screenshot #{index}, but it has {count} — dropping the buttons",
            provider=source.provider,
            index=index,
            count=len(urls),
        )
        await query.edit_message_reply_markup(reply_markup=None)
        return
    url = urls[index]
    image = await _download(context, source, url)
    if image is None:
        await _show_failure(query, source, _SERVICE_DOWN_KEY)
        return
    screen = _add_pick(context, source, url, image)
    if screen is None:
        await query.edit_message_reply_markup(reply_markup=None)
        return
    text, markup = screen
    await query.edit_message_text(text=text, reply_markup=markup)


def _add_pick(
    context: ContextTypes.DEFAULT_TYPE, source: _Source, url: str, image: bytes
) -> clue_screens.Screen | None:
    """The write half of a pick; None if the setup moved on meanwhile."""
    with session_scope(context.bot_data["session_factory"]) as session:
        game = game_service.get_setup_game_for_starter(session, source.chat_id)
        if game is None or game.id != source.game_id or not clue_screens.accepts_panel_taps(game):
            logger.warning(
                "the setup moved on while a clue screenshot downloaded — dropping it",
                game_id=source.game_id,
            )
            return None
        try:
            result = author_list.add(
                session, game, author_list.NewEntry(image, source.provider, url)
            )
            note = clue_screens.add_note(result, source.lang)
        except ValueError:
            logger.opt(exception=True).warning(
                "{provider} clue screenshot {url} isn't a readable image — not added",
                provider=source.provider,
                url=url,
            )
            note = i18n.t("dm_start.clue_not_an_image", source.lang)
        game.setup_step = SetupStep.CONFIRMING
        return clue_screens.panel_screen(session, game, source.lang, note)
