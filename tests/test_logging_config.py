import json
import logging
import sys
from collections.abc import Iterator

import pytest
from loguru import logger
from telegram.error import NetworkError

from nani_pix_bot import log_context
from nani_pix_bot.logging_config import setup_logging

# Shaped like a real bot token (<bot id>:<35 chars>), but not one.
FAKE_BOT_CREDENTIAL = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFake0"
DB_PW = "db-pw-value"
PROXY_PW = "proxy-pw-value"
KNOWN = [FAKE_BOT_CREDENTIAL, DB_PW, PROXY_PW]


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Each test calls setup_logging() in its own body, so the sink binds
    the same sys.stderr capsys reads (pytest swaps it between phases)."""
    root_handlers = logging.root.handlers[:]
    root_level = logging.root.level
    yield
    logger.remove()
    logger.add(sys.stderr)
    logging.root.handlers = root_handlers
    logging.root.setLevel(root_level)
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("apscheduler").setLevel(logging.NOTSET)
    logger.configure(patcher=None, extra={})
    log_context.reset()


def test_known_secrets_are_masked_in_messages(capsys) -> None:
    """Issue #184: httpx logs every request URL, and the Bot API puts the
    token in the URL path; DB/proxy URLs carry passwords the same way."""
    setup_logging("DEBUG", KNOWN)

    logging.getLogger("telegram.ext").debug(
        "Set Bot API URL: https://api.telegram.org/bot%s", FAKE_BOT_CREDENTIAL
    )
    logger.info("connecting to mysql://app:{}@db and http://p:{}@proxy", DB_PW, PROXY_PW)

    err = capsys.readouterr().err
    assert "Set Bot API URL" in err
    assert "mysql://app:***@db" in err
    for secret in KNOWN:
        assert secret not in err


def test_token_shaped_string_is_masked_even_when_not_configured(capsys) -> None:
    setup_logging("DEBUG", [])

    logger.info("POST https://api.telegram.org/bot{}/getMe", FAKE_BOT_CREDENTIAL)

    err = capsys.readouterr().err
    assert "getMe" in err
    assert FAKE_BOT_CREDENTIAL not in err


def test_secret_containing_another_is_masked_whole(capsys) -> None:
    setup_logging("DEBUG", ["pw-value", "outer-pw-value"])

    logger.info("value: outer-pw-value")

    err = capsys.readouterr().err
    assert "value: ***" in err
    assert "outer-" not in err


def test_secrets_are_masked_throughout_an_exception_chain(capsys) -> None:
    setup_logging("DEBUG", KNOWN)

    try:
        try:
            raise OSError(f"proxy auth failed for {PROXY_PW}")
        except OSError as cause:
            raise ConnectionError(
                f"POST https://api.telegram.org/bot{FAKE_BOT_CREDENTIAL}/getMe"
            ) from cause
    except ConnectionError:
        logger.exception("Telegram unreachable")

    err = capsys.readouterr().err
    assert "ConnectionError" in err
    assert "OSError: proxy auth failed for ***" in err
    for secret in KNOWN:
        assert secret not in err


def test_logged_exception_itself_is_left_untouched(capsys) -> None:
    """Callers like db.session_scope log an exception and then re-raise
    it — masking must work on a copy, never the live object."""
    setup_logging("DEBUG", KNOWN)

    try:
        raise ValueError(DB_PW)
    except ValueError as exc:
        logger.exception("failed")
        caught = exc

    assert DB_PW not in capsys.readouterr().err
    assert caught.args == (DB_PW,)


def test_tracebacks_do_not_dump_local_variables(capsys) -> None:
    """loguru's diagnose mode prints locals' values into tracebacks —
    issue #184 saw it render ExtBot[token=...] that way."""
    setup_logging("DEBUG", [])
    config = {"password": "hunter2-not-a-secret"}

    try:
        config["missing"]
    except KeyError:
        logger.exception("lookup failed")

    err = capsys.readouterr().err
    assert "KeyError" in err
    assert "hunter2-not-a-secret" not in err


def test_httpx_routine_request_lines_are_not_logged(capsys) -> None:
    setup_logging("DEBUG", [])
    httpx_logger = logging.getLogger("httpx")

    httpx_logger.info('HTTP Request: POST https://example.invalid/getUpdates "HTTP/1.1 200 OK"')
    httpx_logger.warning("httpx warning still shows")

    err = capsys.readouterr().err
    assert "HTTP Request" not in err
    assert "httpx warning still shows" in err


def test_telegram_errors_rendered_from_their_message_attribute_are_masked(capsys) -> None:
    """PTB's TelegramError.__str__ returns self.message, not args."""
    setup_logging("DEBUG", KNOWN)

    try:
        raise NetworkError(f"proxy http://p:{PROXY_PW}@proxy refused")
    except NetworkError:
        logger.exception("poll failed")

    err = capsys.readouterr().err
    assert "NetworkError" in err
    assert PROXY_PW not in err


def test_apscheduler_job_chatter_is_silenced() -> None:
    """Issue #228: APScheduler's per-job "Added/Removed/Running job"
    INFO lines drowned out every game event at LOG_LEVEL=INFO."""
    setup_logging("INFO")

    assert logging.getLogger("apscheduler").getEffectiveLevel() == logging.WARNING


def _json_lines(err: str) -> list[dict]:
    return [json.loads(line) for line in err.splitlines() if line.strip()]


def test_text_mode_prefixes_game_and_triggering_user(capsys) -> None:
    """Issue #230: context lives in fields; text mode renders it."""
    setup_logging("INFO")
    log_context.reset(user_id=2, username="bob", game_id=88)

    logger.info("guessed {text!r}", text="naruto")

    assert "- [game 88 | 2 @bob] guessed 'naruto'" in capsys.readouterr().err


def test_text_mode_names_the_job_and_omits_an_empty_prefix(capsys) -> None:
    setup_logging("INFO")
    log_context.reset(job="inactivity-nudge-88", game_id=88)
    logger.info("nudge posted")
    log_context.reset()
    logger.info("started")

    err = capsys.readouterr().err
    assert "- [game 88 | job inactivity-nudge-88] nudge posted" in err
    assert "- started" in err


def test_text_mode_falls_back_to_the_full_name(capsys) -> None:
    setup_logging("INFO")
    log_context.reset(user_id=2, user_name="Bob B")

    logger.info("x")

    assert "- [2 Bob B] x" in capsys.readouterr().err


def test_json_mode_flattens_context_kwargs_and_static_fields(capsys) -> None:
    setup_logging("INFO", fmt="json", static={"group_chat_id": -100})
    log_context.reset(user_id=2, username="bob", game_id=88)

    logger.info("guessed {text!r} — 💠 Фрирен", text="naruto")

    (line,) = _json_lines(capsys.readouterr().err)
    assert line["message"] == "guessed 'naruto' — 💠 Фрирен"
    assert line["level"] == "INFO"
    assert line["game_id"] == 88
    assert line["user_id"] == 2
    assert line["text"] == "naruto"
    assert line["group_chat_id"] == -100
    assert {"ts", "logger", "function", "line"} <= line.keys()
    assert "exception" not in line


def test_json_mode_includes_a_masked_traceback(capsys) -> None:
    setup_logging("INFO", KNOWN, fmt="json")

    try:
        raise ValueError(DB_PW)
    except ValueError:
        logger.exception("boom")

    (line,) = _json_lines(capsys.readouterr().err)
    assert "ValueError: ***" in line["exception"]
    assert DB_PW not in json.dumps(line)


def test_secrets_are_masked_inside_extra_fields(capsys) -> None:
    setup_logging("INFO", KNOWN, fmt="json")

    logger.info("fetched {url}", url=f"http://p:{PROXY_PW}@proxy")

    (line,) = _json_lines(capsys.readouterr().err)
    assert line["url"] == "http://p:***@proxy"


def test_stdlib_records_get_the_context_too(capsys) -> None:
    setup_logging("INFO", fmt="json")
    log_context.reset(update_id=7, game_id=88)

    logging.getLogger("telegram.ext").warning("PTB says hi")

    (line,) = _json_lines(capsys.readouterr().err)
    assert line["message"] == "PTB says hi"
    assert line["update_id"] == 7
    assert line["game_id"] == 88
