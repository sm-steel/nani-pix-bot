from unittest.mock import AsyncMock, MagicMock

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest

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
