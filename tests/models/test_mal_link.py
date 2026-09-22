from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.orm import Session

from nani_pix_bot.models.mal_link import ENCRYPTED_TOKEN_LENGTH, MalCredentials, PendingMalLink
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


def test_mal_credentials_access_token_and_refresh_token_columns_have_generous_headroom() -> None:
    """A real MyAnimeList access/refresh token, once Fernet-encrypted,
    exceeded the original 512-char `VARCHAR` in production (issue #185:
    "Data too long for column 'access_token'" against a real account's
    token). Guards the widened length directly against the model's own
    `ENCRYPTED_TOKEN_LENGTH` constant, and against regressing back under
    a reasonable floor, rather than re-hardcoding a specific number here."""
    access_token_type = MalCredentials.__table__.c.access_token.type
    refresh_token_type = MalCredentials.__table__.c.refresh_token.type
    assert isinstance(access_token_type, String)
    assert isinstance(refresh_token_type, String)
    assert access_token_type.length == ENCRYPTED_TOKEN_LENGTH
    assert refresh_token_type.length == ENCRYPTED_TOKEN_LENGTH
    assert ENCRYPTED_TOKEN_LENGTH >= 4096


def test_mal_credentials_round_trips_a_very_long_token(session: Session) -> None:
    """A real MAL access token, Fernet-encrypted, comfortably exceeds the
    old 512-char column — round-trip something similarly long (2000
    chars) to prove nothing along the write/read path truncates it
    silently, independent of whether the test DB dialect itself would
    enforce a VARCHAR limit (SQLite doesn't)."""
    player = _make_player(session)
    now = datetime.now(UTC)
    long_access_token = "a" * 2000
    long_refresh_token = "b" * 2000
    credentials = MalCredentials(
        telegram_user_id=player.telegram_user_id,
        access_token=long_access_token,
        refresh_token=long_refresh_token,
        expires_at=now,
        linked_at=now,
    )
    session.add(credentials)
    session.commit()
    session.expire_all()

    fetched = session.get(MalCredentials, player.telegram_user_id)
    assert fetched is not None
    assert fetched.access_token == long_access_token
    assert fetched.refresh_token == long_refresh_token


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
