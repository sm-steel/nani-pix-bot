"""Application entry point.

Wires up the python-telegram-bot ``Application``, registers command
handlers, and (once games can be active) re-arms any pending 2-day timeout
``JobQueue`` jobs from the database on startup. Left as a stub until the
scaffolding issue's follow-ups (config loading, DB models, command modules)
land — see CLAUDE.md and the GitHub issue tracker.
"""

from __future__ import annotations


def main() -> None:
    """Entry point invoked by the ``nani-pix-bot`` console script."""
    raise NotImplementedError("bot wiring lands in a follow-up issue — see CLAUDE.md")


if __name__ == "__main__":
    main()
