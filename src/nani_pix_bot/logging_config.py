"""loguru setup — see CLAUDE.md's Logging section. Redirects
python-telegram-bot's own stdlib logging into the same sink (the
standard loguru "InterceptHandler" recipe) so everything ends up in one
place with one format, and masks every configured secret on the way
(issue #184).

Two output formats (issue #230), picked by `LOG_FORMAT`: `text` renders
the structured context from log_context.py as a `[game 88 | 2 @bob]`
prefix; `json` writes one flat object per line for centralized collection,
with that context, the call's named-placeholder kwargs and the static
fields (group/topic ids, version) as top-level keys."""

import copy
import json
import logging
import re
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC
from typing import TYPE_CHECKING, Literal, TextIO

from loguru import logger

from nani_pix_bot import log_context

if TYPE_CHECKING:
    from loguru import Message, Record

LogFormat = Literal["text", "json"]
LOG_FORMATS: tuple[LogFormat, ...] = ("text", "json")


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
# extra-field values JSON can carry as they are; every other value is
# turned into its masked str() by the secret filter.
_PLAIN_TYPES = (str, int, float, bool, type(None))


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
    message, string extra fields (named-placeholder kwargs carry typed
    text and URLs into JSON) and exception before the sink formats it.
    Always passes the record through."""
    ordered = sorted({s for s in secrets if s}, key=len, reverse=True)

    def secret_filter(record: "Record") -> bool:
        record["message"] = _mask(record["message"], ordered)
        extra = record["extra"]
        for key, value in extra.items():
            if not isinstance(value, _PLAIN_TYPES):
                # Anything else (an exception passed as `error=exc`, an
                # enum, a URL object) is rendered with str() by both
                # sinks, so mask that rendering instead of letting
                # json.dumps(default=str) write it out unmasked.
                extra[key] = _mask(str(value), ordered)
            elif isinstance(value, str):
                extra[key] = _mask(value, ordered)
        exception = record["exception"]
        if exception is not None and exception.value is not None:
            masked = _masked_exception(exception.value, ordered, {})
            record["exception"] = exception._replace(value=masked)
        return True

    return secret_filter


def _context_prefix(extra: Mapping[str, object]) -> str:
    """`[game 88 | 2 @bob] `, `[game 88 | job inactivity-nudge-88] `, or
    "" with no context. Only who and which game: every other field is
    already in the message, or is JSON-only detail."""
    parts: list[str] = []
    if "game_id" in extra:
        parts.append(f"game {extra['game_id']}")
    if "user_id" in extra:
        name = extra.get("username")
        label = f"@{name}" if name else extra.get("user_name")
        parts.append(f"{extra['user_id']} {label}" if label else str(extra["user_id"]))
    if "job" in extra:
        parts.append(f"job {extra['job']}")
    return f"[{' | '.join(parts)}] " if parts else ""


def _text_format(record: "Record") -> str:
    record["extra"]["_prefix"] = _context_prefix(record["extra"])
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "{extra[_prefix]}<level>{message}</level>\n{exception}"
    )


def _json_line(record: "Record") -> str:
    """One flat object: the record's own facts, then every public extra
    field (context, static fields, the call's kwargs) at top level."""
    entry: dict[str, object] = {
        "ts": record["time"].astimezone(UTC).isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "logger": record["name"],
        "function": record["function"],
        "line": record["line"],
    }
    for key, value in record["extra"].items():
        if not key.startswith("_"):
            entry.setdefault(key, value)
    exception = record["exception"]
    if exception is not None and exception.type is not None:
        entry["exception"] = "".join(
            traceback.format_exception(exception.type, exception.value, exception.traceback)
        )
    return json.dumps(entry, ensure_ascii=False, default=str)


def _json_sink(stream: TextIO) -> Callable[["Message"], None]:
    def sink(message: "Message") -> None:
        stream.write(_json_line(message.record) + "\n")
        stream.flush()

    return sink


def setup_logging(
    level: str = "INFO",
    secrets: Iterable[str] = (),
    *,
    fmt: LogFormat = "text",
    static: Mapping[str, object] | None = None,
) -> None:
    """`static` fields (group/topic ids, version) go on every record; the
    text format doesn't print them, JSON does."""
    logger.remove()
    logger.configure(patcher=log_context.patcher, extra=dict(static or {}))
    secret_filter = _secret_filter(secrets)
    if fmt == "json":
        logger.add(_json_sink(sys.stderr), level=level, filter=secret_filter, format="{message}")
    else:
        # diagnose=False: loguru's diagnose mode prints locals' values into
        # tracebacks (ExtBot[token=...], DB URLs) — see loguru's own warning
        # against enabling it in production.
        logger.add(
            sys.stderr, level=level, filter=secret_filter, format=_text_format, diagnose=False
        )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    # httpx logs one INFO line per HTTP request — every getUpdates long
    # poll, around the clock — which is routine noise, not a game event.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # APScheduler (behind PTB's JobQueue) logs every job added, removed,
    # run and finished at INFO. Those lines drowned out every game event
    # (issue #228). The timer callbacks log what they actually did.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
