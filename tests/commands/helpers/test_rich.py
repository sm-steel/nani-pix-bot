from unittest.mock import AsyncMock, MagicMock

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest, InvalidToken

from nani_pix_bot.commands.helpers import rich


def test_md_escape_neutralizes_markdown_in_names() -> None:
    assert rich.md_escape("a_b*c [x](y) |z|") == r"a\_b\*c \[x\]\(y\) \|z\|"
    assert rich.md_escape("plain") == "plain"


async def test_send_rich_calls_send_rich_message_with_topic_and_buttons() -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock()
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("x", callback_data="y")]])

    await rich.send_rich(bot, rich.RichTarget(555, thread_id=7), "# hi", markup)

    endpoint = bot.do_api_request.await_args_list[0].args[0]
    kwargs = bot.do_api_request.await_args_list[0].kwargs
    assert endpoint == "sendRichMessage"
    assert kwargs["api_kwargs"] == {
        "chat_id": 555,
        "message_thread_id": 7,
        "rich_message": {"markdown": "# hi"},
        "reply_markup": markup,
    }
    assert kwargs["return_type"] is Message


async def test_a_rejected_rich_message_falls_back_to_plain_text(records) -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock(side_effect=BadRequest("unsupported"))
    bot.send_message = AsyncMock()

    await rich.send_rich(bot, rich.RichTarget(555), "# hi")

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args_list[0].kwargs["text"] == "# hi"
    assert any(level == "ERROR" for level, _ in records)


async def test_a_404_for_the_unknown_method_also_falls_back_and_unescapes_names() -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock(side_effect=InvalidToken("Not Found"))
    bot.send_message = AsyncMock()

    await rich.send_rich(bot, rich.RichTarget(555), "# hi @b" + rich.md_escape("_") + "ob")

    assert bot.send_message.await_args_list[0].kwargs["text"] == "# hi @b_ob"


async def test_edit_rich_ignores_not_modified() -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock(side_effect=BadRequest("Message is not modified"))
    bot.edit_message_text = AsyncMock()

    await rich.edit_rich(bot, rich.RichTarget(555, message_id=9), "# same")

    bot.edit_message_text.assert_not_awaited()
    assert bot.do_api_request.await_args_list[0].args[0] == "editMessageText"
    assert bot.do_api_request.await_args_list[0].kwargs["api_kwargs"]["message_id"] == 9


async def test_a_rejected_edit_falls_back_to_unescaped_plain_text(records) -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock(side_effect=InvalidToken("Not Found"))
    bot.edit_message_text = AsyncMock()

    await rich.edit_rich(bot, rich.RichTarget(555, message_id=9), "# a" + rich.md_escape("_") + "b")

    assert bot.edit_message_text.await_args_list[0].kwargs["text"] == "# a_b"
    assert any(level == "ERROR" for level, _ in records)


def test_the_limits_are_telegrams_rich_and_plain_text_limits() -> None:
    assert rich.RICH_LIMIT == 32768
    assert rich.TEXT_LIMIT == 4096


def test_fit_leaves_a_message_within_the_limit_alone(records) -> None:
    text = "x" * rich.RICH_LIMIT

    assert rich.fit(text) == text
    assert records == []


def test_fit_cuts_an_oversized_message_at_a_line_break_and_logs_an_error(records) -> None:
    lines = [f"| row {i} |" for i in range(5000)]
    markdown = "\n".join(lines)

    cut = rich.fit(markdown)

    assert len(cut) <= rich.RICH_LIMIT
    assert cut.endswith("|\n\n…")
    assert cut.removesuffix("\n\n…") in markdown
    assert ("ERROR", f"rich message of {len(markdown)} chars cut to fit") in records


async def test_send_and_edit_send_the_fitted_text() -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock()
    long = "line\n" * rich.RICH_LIMIT

    await rich.send_rich(bot, rich.RichTarget(555), long)
    await rich.edit_rich(bot, rich.RichTarget(555, message_id=9), long)

    for call in bot.do_api_request.await_args_list:
        sent = call.kwargs["api_kwargs"]["rich_message"]["markdown"]
        assert sent == rich.fit(long)


async def test_a_rejected_rich_message_falls_back_cut_to_the_plain_text_limit(records) -> None:
    bot = MagicMock()
    bot.do_api_request = AsyncMock(side_effect=BadRequest("unsupported"))
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    long = "line\n" * 2000  # within RICH_LIMIT, over TEXT_LIMIT

    await rich.send_rich(bot, rich.RichTarget(555), long)
    await rich.edit_rich(bot, rich.RichTarget(555, message_id=9), long)

    for fallback in (bot.send_message, bot.edit_message_text):
        text = fallback.await_args_list[0].kwargs["text"]
        assert len(text) <= rich.TEXT_LIMIT
        assert text.endswith("line\n\n…")
    assert ("ERROR", f"plain-text fallback of {len(long)} chars cut to fit") in records
