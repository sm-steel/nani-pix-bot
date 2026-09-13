"""Screenshot-source selection + gallery browsing — the screenshot-less
/newgame flow's own sub-flow, reached from `search.py`'s
`pick_callback_handler` once identification is staged but no image
exists yet. See MECHANICS.md's "Starting a game" section.

Same-provider path only (the screenshot provider tapped is one the
identification step already has an id for) — cross-provider resolution
(when the starter picks a provider that wasn't used for identification)
is a later ticket's job."""

from dataclasses import dataclass

from loguru import logger
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _client_for_source, _show_preview
from nani_pix_bot.commands.dm_start.keyboards import (
    GalleryPage,
    parse_screenshot_more_callback_data,
    parse_screenshot_pick_callback_data,
    parse_screenshot_source_callback_data,
    screenshot_gallery_keyboard,
    screenshot_source_keyboard,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.search import jikan, shikimori, tmdb

GALLERY_PAGE_SIZE = 5

# One provider id column per screenshot-capable provider — see
# models/game.py's per-provider *_id columns.
_ID_ATTRS = {"shikimori": "shikimori_id", "jikan": "jikan_id", "tmdb": "tmdb_id"}
# Module references, not bound function references — a plain
# {"shikimori": shikimori.screenshots, ...} dict would capture the
# function object at import time, which stops respecting
# monkeypatch.setattr(shikimori, "screenshots", ...) in tests (and,
# more generally, would go stale if a provider module ever reassigned
# its own screenshots name after import).
_SCREENSHOT_MODULES = {"shikimori": shikimori, "jikan": jikan, "tmdb": tmdb}


async def _fetch_screenshots(provider: str, client, provider_id: int) -> list[str]:
    return await _SCREENSHOT_MODULES[provider].screenshots(client, provider_id)


@dataclass(frozen=True)
class GalleryTarget:
    """Where a gallery page is being sent and which provider/offset
    it's paginating — bundled into one object so `_show_gallery_page`
    doesn't need a 6-argument signature (a qlty "many parameters"
    smell). `cross_provider` defaults to False since every caller in
    this ticket is same-provider; ticket 8 will pass True from its own
    cross-provider-resolution path."""

    chat_id: int
    provider: str
    offset: int
    cross_provider: bool = False


def _screenshot_capable_providers(game) -> list[str]:
    """Providers with both a screenshots() capability and a known id
    for this game, same-provider-as-identification first (so the
    common case — screenshot source matches identification source —
    needs no cross-provider search at all)."""
    candidates = [
        provider
        for provider in ("shikimori", "jikan", "tmdb")
        if getattr(game, _ID_ATTRS[provider])
    ]
    if game.source in candidates:
        candidates.remove(game.source)
        candidates.insert(0, game.source)
    return candidates


async def start_screenshot_picker(context: ContextTypes.DEFAULT_TYPE, game, lang: str) -> None:
    """Entry point from `search.py`'s `pick_callback_handler` (and
    `manual.py`'s synonym step) once identification is staged with no
    image yet — shows the screenshot-source-selection keyboard, or (no
    screenshot-capable provider at all — e.g. AniList/manual
    identification) goes straight to asking for an upload."""
    providers = _screenshot_capable_providers(game)
    if not providers:
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        await context.bot.send_message(
            chat_id=game.starter_id, text=i18n.t("dm_start.ask_new_photo", lang)
        )
        return
    game.setup_step = SetupStep.PICKING_SCREENSHOT
    await context.bot.send_message(
        chat_id=game.starter_id,
        text=i18n.t("dm_start.pick_screenshot_source_prompt", lang),
        reply_markup=screenshot_source_keyboard(providers, lang),
    )


async def screenshot_upload_instead_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ "Upload my own instead" — offered on both the source-selection
    keyboard and the gallery keyboard, but registered as its own
    handler (rather than duplicated in each of theirs) since the
    behavior is identical regardless of which screen it was tapped
    from: fall back to the traditional upload flow, reusing the
    existing "Change image" photo-replacement handling."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return
        game.setup_step = SetupStep.AWAITING_PHOTO_CHANGE
        await query.edit_message_text(i18n.t("dm_start.ask_new_photo", lang))


async def screenshot_source_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A screenshot-source button tapped from `start_screenshot_picker`'s
    keyboard."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return

        provider = parse_screenshot_source_callback_data(query.data)
        provider_id = getattr(game, _ID_ATTRS[provider]) if provider else None
        if provider is None or provider_id is None:
            return

        client = _client_for_source(context, provider)
        urls = await _fetch_screenshots(provider, client, provider_id)
        logger.debug("Game {}: fetched {} {} screenshot(s)", game.id, len(urls), provider)
        target = GalleryTarget(chat_id=game.starter_id, provider=provider, offset=0)
        await _show_gallery_page(context, target, urls, lang)

    await query.edit_message_text(i18n.t("dm_start.screenshot_source_picked", lang))


async def screenshot_gallery_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """A numbered screenshot or "More screenshots" button tapped from
    the gallery's own keyboard."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = query.from_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game = game_service.get_setup_game_for_starter(session, user.id)
        if game is None:
            return
        reply_key = await _dispatch_gallery_action(context, session, game, query.data, lang)

    if reply_key is not None:
        await query.edit_message_text(i18n.t(reply_key, lang))


async def _dispatch_gallery_action(
    context: ContextTypes.DEFAULT_TYPE, session, game, data: str, lang: str
) -> str | None:
    """Runs the gallery action `data` encodes (a "More screenshots" page
    or a numbered pick) and returns the i18n key for the resulting
    message, or None for a stale-button no-op — split out of
    `screenshot_gallery_callback_handler` itself to keep that handler's
    own return count under qlty's "many returns" threshold."""
    more = parse_screenshot_more_callback_data(data)
    if more is not None:
        await _handle_more_screenshots(context, game, more, lang)
        return "dm_start.more_screenshots_sent"

    picked = parse_screenshot_pick_callback_data(data)
    if picked is None:
        return None
    picked_ok = await _handle_screenshot_pick(context, session, game, picked, lang)
    # picked_ok is False for a stale button (e.g. a re-fetch returned
    # fewer results than the tapped index) — a no-op, same as `picked
    # is None` above.
    return "dm_start.preview_sent" if picked_ok else None


async def _handle_more_screenshots(
    context: ContextTypes.DEFAULT_TYPE, game, more: tuple[str, int], lang: str
) -> None:
    provider, offset = more
    provider_id = getattr(game, _ID_ATTRS[provider])
    client = _client_for_source(context, provider)
    urls = await _fetch_screenshots(provider, client, provider_id)
    target = GalleryTarget(chat_id=game.starter_id, provider=provider, offset=offset)
    await _show_gallery_page(context, target, urls, lang)


async def _handle_screenshot_pick(
    context: ContextTypes.DEFAULT_TYPE, session, game, picked: tuple[str, int], lang: str
) -> bool:
    """Downloads the picked screenshot's bytes and shows the
    confirmation preview. Returns False (a no-op, caller shouldn't edit
    the triggering message) if the index is now out of range."""
    provider, index = picked
    provider_id = getattr(game, _ID_ATTRS[provider])
    client = _client_for_source(context, provider)
    urls = await _fetch_screenshots(provider, client, provider_id)
    if index >= len(urls):
        return False

    # These are external URLs (Shikimori/Jikan/TMDB), not Telegram
    # file_ids — the gallery album itself lets Telegram fetch them
    # server-side, but storing one as original_image needs the actual
    # bytes downloaded ourselves.
    download_client = context.bot_data["search_client"]
    response = await download_client.get(urls[index])
    response.raise_for_status()
    game.original_image = response.content
    game.screenshot_source = provider
    logger.debug("Game {}: picked {} screenshot #{}", game.id, provider, index + 1)

    await _show_preview(context, session, game, lang)
    return True


async def _show_gallery_page(
    context: ContextTypes.DEFAULT_TYPE, target: GalleryTarget, urls: list[str], lang: str
) -> None:
    """Sends an album of up to GALLERY_PAGE_SIZE numbered screenshots
    starting at `target.offset`, followed by the gallery's own buttons
    message. Telegram fetches media-group photos server-side from a URL
    directly — no need to download bytes ourselves until one is
    actually picked."""
    shown = urls[target.offset : target.offset + GALLERY_PAGE_SIZE]
    if not shown:
        await context.bot.send_message(
            chat_id=target.chat_id, text=i18n.t("dm_start.no_screenshots_available", lang)
        )
        return

    media = [
        InputMediaPhoto(media=url, caption=str(target.offset + i + 1))
        for i, url in enumerate(shown)
    ]
    await context.bot.send_media_group(chat_id=target.chat_id, media=media)

    has_more = len(urls) > target.offset + len(shown)
    page = GalleryPage(
        provider=target.provider,
        offset=target.offset,
        count=len(shown),
        has_more=has_more,
        cross_provider=target.cross_provider,
    )
    await context.bot.send_message(
        chat_id=target.chat_id,
        text=i18n.t("dm_start.pick_screenshot_prompt", lang),
        reply_markup=screenshot_gallery_keyboard(page, lang),
    )
