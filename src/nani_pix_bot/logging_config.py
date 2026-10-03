"""loguru setup — see CLAUDE.md's Logging section. Redirects
python-telegram-bot's own stdlib logging into the same sink (the
standard loguru "InterceptHandler" recipe) so everything ends up in one
place with one format."""

import logging
import re
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: int | str = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


# A Bot API token (<bot id>:<35-char secret>) — it sits in every Bot API
# request URL, so httpx/python-telegram-bot log lines and tracebacks carry
# it (issue #184).
_BOT_TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]{35}")


def _redacting_stderr_sink(message: str) -> None:
    """Redacts the fully formatted record — message and traceback alike —
    and looks sys.stderr up per write rather than binding it once."""
    sys.stderr.write(_BOT_TOKEN_RE.sub("<BOT_TOKEN>", message))


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    # diagnose=False: loguru's diagnose mode prints locals' values into
    # tracebacks (ExtBot[token=...], DB URLs) — see loguru's own warning
    # against enabling it in production.
    logger.add(_redacting_stderr_sink, level=level, diagnose=False)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    # httpx logs one INFO line per HTTP request — every getUpdates long
    # poll, around the clock — which is routine noise, not a game event.
    logging.getLogger("httpx").setLevel(logging.WARNING)
