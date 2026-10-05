from unittest.mock import MagicMock

from nani_pix_bot.commands.helpers.actor import describe_user


def _user(*, username: str | None, full_name: str = "Bob B") -> MagicMock:
    user = MagicMock()
    user.id = 5
    user.username = username
    user.full_name = full_name
    return user


def test_describe_user_uses_the_username() -> None:
    assert describe_user(_user(username="bob")) == "5 (@bob)"


def test_describe_user_falls_back_to_the_full_name() -> None:
    assert describe_user(_user(username=None)) == "5 (Bob B)"


def test_describe_user_handles_no_user() -> None:
    assert describe_user(None) == "?"
