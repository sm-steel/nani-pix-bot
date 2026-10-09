"""/version — the running build's version and its hand-written release notes
(services/release_notes.py, rules in docs/release-notes.md) as one rich
message, with ◀ n/N ▶ paging back through earlier releases (ver:<page>,
edited in place). No scope/membership gating: usable in both DM and the
group game topic, same posture as /help's non-redirect branch (see
MECHANICS.md's onboarding.help entry for that precedent)."""

from loguru import logger
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.commands.helpers.paging import nav_row
from nani_pix_bot.commands.helpers.rich import RichTarget, edit_rich, send_rich
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, release_notes, settings, version
from nani_pix_bot.services.release_notes import Release

PREFIX = "ver:"


def page_data(page: int) -> str:
    return f"{PREFIX}{page}"


def parse_page(data: str) -> int | None:
    """Client-controlled, so only a bare bounded int after the prefix."""
    raw = data.removeprefix(PREFIX) if data.startswith(PREFIX) else ""
    return int(raw) if raw.isdecimal() and int(raw) <= MAX_ID else None


def _page_markdown(release: Release, running_version: str, lang: str) -> str:
    if release.version is not None:
        heading = f"## v{release.version}"
        if release.date is not None:
            heading += f" · {release.date}"
        return f"{heading}\n\n{release.markdown}"
    if not release.markdown:
        return i18n.t("version.no_notes", lang, version=running_version)
    return i18n.t("version.reply", lang, version=running_version, notes=release.markdown)


def page_view(
    releases: list[Release], page: int, running_version: str, lang: str
) -> tuple[str, InlineKeyboardMarkup | None]:
    """`page` must already be in range."""
    markdown = _page_markdown(releases[page], running_version, lang)
    nav = nav_row(page_data, page, len(releases), lang)
    return markdown, InlineKeyboardMarkup([nav]) if nav else None


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    with session_scope(context.bot_data["session_factory"]) as session:
        return settings.get_language(session)


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return
    lang = _lang(context)
    running_version = version.installed_version()
    markdown, markup = page_view(release_notes.load(lang), 0, running_version, lang)
    logger.info("sent /version — running {version}", version=running_version)
    target = RichTarget(message.chat_id, thread_id=message.message_thread_id)
    await send_rich(context.bot, target, markdown, markup)


async def version_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    page = parse_page(query.data or "")
    if page is None or query.message is None:
        logger.warning("ignored a malformed or stale /version tap {data!r}", data=query.data)
        await query.answer()
        return
    await query.answer()
    lang = _lang(context)
    releases = release_notes.load(lang)
    page = min(page, len(releases) - 1)  # a tap from before a rotation
    markdown, markup = page_view(releases, page, version.installed_version(), lang)
    logger.info(
        "paged /version to {page}", page=page + 1, release=releases[page].version or "current"
    )
    target = RichTarget(query.message.chat.id, message_id=query.message.message_id)
    await edit_rich(context.bot, target, markdown, markup)
