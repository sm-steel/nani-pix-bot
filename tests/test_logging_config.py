import logging
import sys
from collections.abc import Iterator

import pytest
from loguru import logger

from nani_pix_bot.logging_config import setup_logging

# Shaped like a real bot token (<bot id>:<35 chars>), but not one.
FAKE_BOT_CREDENTIAL = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFake0"


@pytest.fixture
def configured(capsys) -> Iterator[pytest.CaptureFixture[str]]:
    root_handlers = logging.root.handlers[:]
    root_level = logging.root.level
    setup_logging("DEBUG")
    yield capsys
    logger.remove()
    logger.add(sys.stderr)
    logging.root.handlers = root_handlers
    logging.root.setLevel(root_level)
    logging.getLogger("httpx").setLevel(logging.NOTSET)


def test_bot_token_is_redacted_from_log_lines(configured) -> None:
    """Issue #184: httpx logs every request URL, and the Bot API puts the
    token in the URL path."""
    logging.getLogger("telegram.ext").debug(
        "Set Bot API URL: https://api.telegram.org/bot%s", FAKE_BOT_CREDENTIAL
    )

    err = configured.readouterr().err
    assert "Set Bot API URL" in err
    assert FAKE_BOT_CREDENTIAL not in err


def test_bot_token_is_redacted_from_tracebacks(configured) -> None:
    try:
        raise ConnectionError(f"POST https://api.telegram.org/bot{FAKE_BOT_CREDENTIAL}/getMe")
    except ConnectionError:
        logger.exception("Telegram unreachable")

    err = configured.readouterr().err
    assert "ConnectionError" in err
    assert FAKE_BOT_CREDENTIAL not in err


def test_tracebacks_do_not_dump_local_variables(configured) -> None:
    """loguru's diagnose mode prints locals' values into tracebacks —
    issue #184 saw it render ExtBot[token=...] that way."""
    config = {"password": "hunter2-not-a-secret"}
    try:
        config["missing"]
    except KeyError:
        logger.exception("lookup failed")

    err = configured.readouterr().err
    assert "KeyError" in err
    assert "hunter2-not-a-secret" not in err


def test_httpx_routine_request_lines_are_not_logged(configured) -> None:
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.info('HTTP Request: POST https://example.invalid/getUpdates "HTTP/1.1 200 OK"')
    httpx_logger.warning("httpx warning still shows")

    err = configured.readouterr().err
    assert "HTTP Request" not in err
    assert "httpx warning still shows" in err
