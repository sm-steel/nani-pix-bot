from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nani_pix_bot.models.mal_link import MalCredentials, PendingMalLink
from nani_pix_bot.models.player import Player


def _make_player(session: Session, telegram_user_id: int = 1) -> Player:
    player = Player(telegram_user_id=telegram_user_id)
    session.add(player)
    session.commit()
    return player


def test_mal_credentials_round_trips_through_the_database(session: Session) -> None:
    player = _make_player(session)
    now = datetime.now(UTC)
    credentials = MalCredentials(
        telegram_user_id=player.telegram_user_id,
        access_token="encrypted-access",  # noqa: S106 - test fixture, not a real token
        refresh_token="encrypted-refresh",  # noqa: S106 - test fixture, not a real token
        expires_at=now,
        mal_username="UnstableFractal",
        linked_at=now,
    )
    session.add(credentials)
    session.commit()
    session.expire_all()

    fetched = session.get(MalCredentials, player.telegram_user_id)
    assert fetched is not None
    assert fetched.access_token == "encrypted-access"  # noqa: S105 - test fixture, not a real token
    assert fetched.refresh_token == "encrypted-refresh"  # noqa: S105 - test fixture, not a real token
    assert fetched.mal_username == "UnstableFractal"


def test_mal_credentials_mal_username_defaults_to_none(session: Session) -> None:
    player = _make_player(session)
    now = datetime.now(UTC)
    credentials = MalCredentials(
        telegram_user_id=player.telegram_user_id,
        access_token="a",  # noqa: S106 - test fixture, not a real token
        refresh_token="b",  # noqa: S106 - test fixture, not a real token
        expires_at=now,
        linked_at=now,
    )
    session.add(credentials)
    session.commit()
    session.expire_all()

    fetched = session.get(MalCredentials, player.telegram_user_id)
    assert fetched is not None
    assert fetched.mal_username is None


def test_pending_mal_link_round_trips_through_the_database(session: Session) -> None:
    player = _make_player(session)
    now = datetime.now(UTC)
    pending = PendingMalLink(
        telegram_user_id=player.telegram_user_id,
        state="state-token",
        code_verifier="verifier-token",
        created_at=now,
    )
    session.add(pending)
    session.commit()
    session.expire_all()

    fetched = session.get(PendingMalLink, player.telegram_user_id)
    assert fetched is not None
    assert fetched.state == "state-token"
    assert fetched.code_verifier == "verifier-token"
