"""Structured log context (issue #230): who and what a log line is about,
attached centrally instead of written into every message.

One `ContextVar` holds the fields of the unit of work in progress. It is
reset at the start of every Telegram update (commands/helpers/log_scope.py)
and every timer job (jobs/timers/_shared.py's job_log_scope), and the game
lookups in services/game/state.py bind the game they found. `patcher`
copies the fields into each record's `extra`, where the text sink renders
them as a `[game 88 | 2 @bob]` prefix and the JSON sink emits them as
fields (logging_config.py).

A ContextVar rather than loguru's own `contextualize()`: that is a context
manager, and none of the places that learn the context (a pre-handler, a
lookup function) wrap the work that follows it. asyncio copies context
into every task, so concurrent jobs never see each other's fields.

Plain Python on purpose: services/ imports this, and must stay
Telegram-agnostic."""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from loguru import Record

_context: ContextVar[dict[str, object]] = ContextVar("log_context")


class _GameLike(Protocol):
    """A Game row, read-only — structural so this module needn't import
    the ORM models."""

    @property
    def id(self) -> int: ...


def reset(**fields: object) -> None:
    """Start a new unit of work: replace the whole context, so nothing from
    the previous update or from whatever scheduled a job leaks in."""
    _context.set(dict(fields))


def bind(**fields: object) -> None:
    """Add fields to the current unit of work's context."""
    _context.set({**_context.get({}), **fields})


def bind_game(game: _GameLike | None) -> None:
    """Attach the game this unit of work is about; a no-op for None (a
    lookup that found nothing). Only the id: a status captured here would
    go stale the moment the same update activates or ends the game."""
    if game is None:
        return
    bind(game_id=game.id)


def current() -> dict[str, object]:
    return dict(_context.get({}))


def patcher(record: "Record") -> None:
    """Copy the context into the record without overriding anything the
    call passed explicitly: `logger.info("…", game_id=4)` keeps 4."""
    extra = record["extra"]
    for key, value in _context.get({}).items():
        extra.setdefault(key, value)
