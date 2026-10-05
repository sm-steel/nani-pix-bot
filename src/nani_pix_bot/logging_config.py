"""loguru setup — see CLAUDE.md's Logging section. Redirects
python-telegram-bot's own stdlib logging into the same sink (the
standard loguru "InterceptHandler" recipe) so everything ends up in one
place with one format, and masks every configured secret on the way
(issue #184)."""

import copy
import logging
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record


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
# request URL. The shape-based catch for one that isn't in the configured
# list (issue #184).
_BOT_TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]{35}")
_MASK = "***"


def _mask(text: str, secrets: Sequence[str]) -> str:
    """`secrets` must be sorted longest-first, so a secret that contains
    another is masked whole rather than leaving its remainder visible."""
    for secret in secrets:
        text = text.replace(secret, _MASK)
    return _BOT_TOKEN_RE.sub(_MASK, text)


def _attribute_names(obj: object) -> set[str]:
    """Instance attributes held in __dict__ or in __slots__ anywhere in the
    MRO (PTB's TelegramError keeps `message` in a slot)."""
    names = set(getattr(obj, "__dict__", {}))
    for cls in type(obj).__mro__:
        slots = getattr(cls, "__slots__", ())
        names.update((slots,) if isinstance(slots, str) else slots)
    return names


def _masked_exception(
    exc: BaseException, secrets: Sequence[str], memo: dict[int, BaseException]
) -> BaseException:
    """A copy of `exc`, and of its whole cause/context chain, with every
    string in its args and its instance attributes masked (PTB's
    TelegramError renders its `message` slot, not args). Never mutates `exc`
    itself: callers like db.session_scope log and then re-raise it. An
    exception that renders itself from something else (SQLAlchemy's
    StatementError formats .orig/statement/params) is masked only as far
    as its args, attributes and chain go."""
    if id(exc) in memo:
        return memo[id(exc)]
    try:
        clone = copy.copy(exc)
    except Exception:  # some exceptions can't be rebuilt from their args
        return exc
    memo[id(exc)] = clone
    clone.args = tuple(_mask(a, secrets) if isinstance(a, str) else a for a in exc.args)
    for name in _attribute_names(clone):
        value = getattr(clone, name, None)
        if isinstance(value, str):
            setattr(clone, name, _mask(value, secrets))
    clone.__traceback__ = exc.__traceback__
    clone.__suppress_context__ = exc.__suppress_context__
    if exc.__cause__ is not None:
        clone.__cause__ = _masked_exception(exc.__cause__, secrets, memo)
    if exc.__context__ is not None:
        clone.__context__ = _masked_exception(exc.__context__, secrets, memo)
    return clone


def _secret_filter(secrets: Iterable[str]) -> Callable[["Record"], bool]:
    """A loguru filter that masks every known secret (config.py's
    Config.secret_values()) and any token-shaped string in the record's
    message and exception before the sink formats it. Always passes the
    record through."""
    ordered = sorted({s for s in secrets if s}, key=len, reverse=True)

    def secret_filter(record: "Record") -> bool:
        record["message"] = _mask(record["message"], ordered)
        exception = record["exception"]
        if exception is not None and exception.value is not None:
            masked = _masked_exception(exception.value, ordered, {})
            record["exception"] = exception._replace(value=masked)
        return True

    return secret_filter


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    logger.remove()
    # diagnose=False: loguru's diagnose mode prints locals' values into
    # tracebacks (ExtBot[token=...], DB URLs) — see loguru's own warning
    # against enabling it in production.
    logger.add(sys.stderr, level=level, filter=_secret_filter(secrets), diagnose=False)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    # httpx logs one INFO line per HTTP request — every getUpdates long
    # poll, around the clock — which is routine noise, not a game event.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # APScheduler (behind PTB's JobQueue) logs every job added, removed,
    # run and finished at INFO. Those lines drowned out every game event
    # (issue #228). The timer callbacks log what they actually did.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
