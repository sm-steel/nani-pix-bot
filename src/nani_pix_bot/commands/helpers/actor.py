"""How the person behind an update appears in log lines — the Telegram
side of services/players.py's describe_person(), which services use for a
player they only know by id."""

from telegram import User

from nani_pix_bot.services.players import describe_person


def describe_user(user: User | None) -> str:
    """`5 (@bob)`, else `5 (Bob B)`; `?` when the update has no user."""
    if user is None:
        return "?"
    return describe_person(user.id, username=user.username, name=user.full_name)
