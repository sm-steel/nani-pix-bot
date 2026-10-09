from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import version as version_command
from nani_pix_bot.commands.helpers.paging import NOOP
from nani_pix_bot.services import i18n, release_notes, settings
from nani_pix_bot.services.release_notes import Release

CURRENT = Release(None, None, "### ✨ New\n- Shiny.")
PREVIOUS = [
    Release("1.2.0", "2026-10-08", "### 🛠 Fixed\n- Mended."),
    Release("1.1.0", "2026-10-01", "### ✨ New\n- Older."),
]
# Telegram's rich-message size limit (Bot API): a transport ceiling, not a content rule.
RICH_MESSAGE_LIMIT = 32768


@pytest.fixture(autouse=True)
def _running_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version_command.version, "installed_version", lambda: "1.3.0")


def _notes(monkeypatch: pytest.MonkeyPatch, releases: list[Release]) -> list[str]:
    langs: list[str] = []

    def fake_load(lang: str) -> list[Release]:
        langs.append(lang)
        return releases

    monkeypatch.setattr(version_command.release_notes, "load", fake_load)
    return langs


def _make_update(*, thread_id: int | None = None) -> MagicMock:
    update = MagicMock()
    update.message.chat_id = 555
    update.message.message_thread_id = thread_id
    return update


def _make_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    return context


async def _send(update: MagicMock, context: MagicMock) -> AsyncMock:
    with patch.object(version_command, "send_rich", new=AsyncMock()) as sent:
        await version_command.version_command(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    return sent


def _tap(data: str) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.chat.id = 555
    update.callback_query.message.message_id = 9
    return update


async def _edit(update: MagicMock, context: MagicMock) -> AsyncMock:
    with patch.object(version_command, "edit_rich", new=AsyncMock()) as edited:
        await version_command.version_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    return edited


def _args(mock: AsyncMock) -> tuple:
    mock.assert_awaited_once()
    return mock.await_args_list[0].args


def _buttons(markup: InlineKeyboardMarkup | None) -> list[tuple[str, str]]:
    assert markup is not None
    return [(b.text, str(b.callback_data)) for row in markup.inline_keyboard for b in row]


# --- /version ---------------------------------------------------------------


async def test_shows_the_running_version_and_the_current_notes(
    session_factory, monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    _notes(monkeypatch, [CURRENT, *PREVIOUS])

    sent = await _send(_make_update(), _make_context(session_factory))

    _bot, target, markdown, markup = _args(sent)
    assert (target.chat_id, target.thread_id) == (555, None)
    assert markdown == i18n.t("version.reply", "en", version="1.3.0", notes=CURRENT.markdown)
    assert markdown.startswith("🤖 **nani-pix-bot** v1.3.0")
    assert _buttons(markup) == [("1/3", NOOP), (i18n.t("achievements.next_page", "en"), "ver:1")]
    line = next(r for r in log_records if r.message.startswith("sent /version"))
    assert (line.level, line.extra["version"]) == ("INFO", "1.3.0")


async def test_replies_in_the_game_topic_too(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _notes(monkeypatch, [CURRENT])

    sent = await _send(_make_update(thread_id=7), _make_context(session_factory))

    _bot, target, _markdown, markup = _args(sent)
    assert target.thread_id == 7
    assert markup is None  # nothing to page back to


async def test_an_empty_current_file_says_nothing_is_noted(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _notes(monkeypatch, [Release(None, None, ""), *PREVIOUS])

    sent = await _send(_make_update(), _make_context(session_factory))

    markdown = _args(sent)[2]
    assert markdown == i18n.t("version.no_notes", "en", version="1.3.0")
    assert "Nothing noted" in markdown


async def test_uses_the_configured_language(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    langs = _notes(monkeypatch, [Release(None, None, "")])
    with session_factory() as session:
        settings.set_language(session, "ru")
        session.commit()

    sent = await _send(_make_update(), _make_context(session_factory))

    assert langs == ["ru"]
    assert _args(sent)[2] == i18n.t("version.no_notes", "ru", version="1.3.0")


async def test_ignores_an_update_with_no_message(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _notes(monkeypatch, [CURRENT])
    update = MagicMock()
    update.message = None

    sent = await _send(update, _make_context(session_factory))

    sent.assert_not_awaited()


# --- paging -----------------------------------------------------------------


async def test_paging_shows_a_previous_release_under_its_own_heading(
    session_factory, monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    _notes(monkeypatch, [CURRENT, *PREVIOUS])
    update = _tap("ver:1")

    edited = await _edit(update, _make_context(session_factory))

    update.callback_query.answer.assert_awaited_once()
    _bot, target, markdown, markup = _args(edited)
    assert (target.chat_id, target.message_id) == (555, 9)
    assert markdown == "## v1.2.0 · 2026-10-08\n\n### 🛠 Fixed\n- Mended."
    assert "nani-pix-bot" not in markdown
    assert _buttons(markup) == [
        (i18n.t("achievements.prev", "en"), "ver:0"),
        ("2/3", NOOP),
        (i18n.t("achievements.next_page", "en"), "ver:2"),
    ]
    line = next(r for r in log_records if r.message.startswith("paged /version"))
    assert (line.level, line.extra["page"], line.extra["release"]) == ("INFO", 2, "1.2.0")


async def test_paging_back_to_page_zero_shows_the_running_version(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _notes(monkeypatch, [CURRENT, *PREVIOUS])

    edited = await _edit(_tap("ver:0"), _make_context(session_factory))

    assert _args(edited)[2].startswith("🤖 **nani-pix-bot** v1.3.0")


async def test_a_page_past_the_end_shows_the_oldest_release(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _notes(monkeypatch, [CURRENT, *PREVIOUS])

    edited = await _edit(_tap("ver:9"), _make_context(session_factory))

    assert _args(edited)[2].startswith("## v1.1.0 · 2026-10-01")


@pytest.mark.parametrize("data", ["ver:", "ver:x", "ver:-1", "ver:1:2", f"ver:{2**63}", "lb:1"])
async def test_a_forged_tap_is_answered_and_ignored(
    session_factory, monkeypatch: pytest.MonkeyPatch, data: str, log_records
) -> None:
    _notes(monkeypatch, [CURRENT, *PREVIOUS])
    update = _tap(data)

    edited = await _edit(update, _make_context(session_factory))

    edited.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once()
    assert any(r.level == "WARNING" for r in log_records)


def test_page_data_round_trips() -> None:
    assert version_command.parse_page(version_command.page_data(3)) == 3


# --- the real notes ---------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_every_real_page_fits_in_one_rich_message(lang: str) -> None:
    releases = release_notes.load(lang)
    for page in range(len(releases)):
        markdown, _markup = version_command.page_view(releases, page, "1.3.0", lang)
        assert len(markdown) <= RICH_MESSAGE_LIMIT, page
