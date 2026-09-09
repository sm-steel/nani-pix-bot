"""loguru setup — see CLAUDE.md's Logging section. Redirects
python-telegram-bot's own stdlib logging into the same sink (the
standard loguru "InterceptHandler" recipe) so everything ends up in one
place with one format."""

import logging
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


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
