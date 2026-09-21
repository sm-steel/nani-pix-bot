"""Tests for services.mal_link — hardcoded test token values trigger S105/S106 warnings
(Possible hardcoded password), which are false positives in test data."""
# ruff: noqa: S105, S106

from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.services import mal_link
from nani_pix_bot.services.mal_link import CredentialsData, DecryptedCredentials


def _make_player(session: Session, telegram_user_id: int = 1) -> Player:
    player = Player(telegram_user_id=telegram_user_id)
    session.add(player)
    session.commit()
    return player


def test_upsert_pending_link_creates_a_row(session: Session) -> None:
    player = _make_player(session)

    mal_link.upsert_pending_link(session, player.telegram_user_id, state="s1", code_verifier="v1")
    session.commit()

    pending = mal_link.get_pending_link(session, player.telegram_user_id)
    assert pending is not None
    assert pending.state == "s1"
    assert pending.code_verifier == "v1"


def test_upsert_pending_link_overwrites_an_existing_row(session: Session) -> None:
    player = _make_player(session)
    mal_link.upsert_pending_link(session, player.telegram_user_id, state="s1", code_verifier="v1")
    session.commit()

    mal_link.upsert_pending_link(session, player.telegram_user_id, state="s2", code_verifier="v2")
    session.commit()

    pending = mal_link.get_pending_link(session, player.telegram_user_id)
    assert pending is not None
    assert pending.state == "s2"
    assert pending.code_verifier == "v2"


def test_get_pending_link_returns_none_when_absent(session: Session) -> None:
    player = _make_player(session)
    assert mal_link.get_pending_link(session, player.telegram_user_id) is None


def test_delete_pending_link_removes_the_row(session: Session) -> None:
    player = _make_player(session)
    mal_link.upsert_pending_link(session, player.telegram_user_id, state="s1", code_verifier="v1")
    session.commit()

    mal_link.delete_pending_link(session, player.telegram_user_id)
    session.commit()

    assert mal_link.get_pending_link(session, player.telegram_user_id) is None


def test_delete_pending_link_is_a_noop_when_absent(session: Session) -> None:
    player = _make_player(session)
    mal_link.delete_pending_link(session, player.telegram_user_id)  # must not raise
    session.commit()


def test_upsert_and_get_credentials_round_trips_decrypted(session: Session) -> None:
    player = _make_player(session)
    key = Fernet.generate_key().decode()
    # DATETIME columns round-trip as naive UTC through SQLite
    expires_at = datetime.now(UTC).replace(tzinfo=None)

    mal_link.upsert_credentials(
        session,
        player.telegram_user_id,
        data=CredentialsData(
            encryption_key=key,
            access_token="real-access-token",
            refresh_token="real-refresh-token",
            expires_at=expires_at,
            mal_username="UnstableFractal",
        ),
    )
    session.commit()

    credentials = mal_link.get_credentials(session, player.telegram_user_id, encryption_key=key)
    assert credentials == DecryptedCredentials(
        access_token="real-access-token",
        refresh_token="real-refresh-token",
        expires_at=expires_at,
        mal_username="UnstableFractal",
    )


def test_credentials_are_stored_encrypted_not_plaintext(session: Session) -> None:
    player = _make_player(session)
    key = Fernet.generate_key().decode()

    mal_link.upsert_credentials(
        session,
        player.telegram_user_id,
        data=CredentialsData(
            encryption_key=key,
            access_token="real-access-token",
            refresh_token="real-refresh-token",
            expires_at=datetime.now(UTC).replace(tzinfo=None),
            mal_username=None,
        ),
    )
    session.commit()
    session.expire_all()

    from nani_pix_bot.models.mal_link import MalCredentials

    raw = session.get(MalCredentials, player.telegram_user_id)
    assert raw is not None
    assert raw.access_token != "real-access-token"
    assert raw.refresh_token != "real-refresh-token"


def test_upsert_credentials_overwrites_an_existing_row(session: Session) -> None:
    player = _make_player(session)
    key = Fernet.generate_key().decode()
    mal_link.upsert_credentials(
        session,
        player.telegram_user_id,
        data=CredentialsData(
            encryption_key=key,
            access_token="old-access",
            refresh_token="old-refresh",
            expires_at=datetime.now(UTC).replace(tzinfo=None),
            mal_username="OldName",
        ),
    )
    session.commit()

    mal_link.upsert_credentials(
        session,
        player.telegram_user_id,
        data=CredentialsData(
            encryption_key=key,
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=datetime.now(UTC).replace(tzinfo=None),
            mal_username="NewName",
        ),
    )
    session.commit()

    credentials = mal_link.get_credentials(session, player.telegram_user_id, encryption_key=key)
    assert credentials is not None
    assert credentials.access_token == "new-access"
    assert credentials.mal_username == "NewName"


def test_get_credentials_returns_none_when_absent(session: Session) -> None:
    player = _make_player(session)
    key = Fernet.generate_key().decode()
    assert mal_link.get_credentials(session, player.telegram_user_id, encryption_key=key) is None


def test_get_credentials_raises_invalid_token_on_wrong_key(session: Session) -> None:
    player = _make_player(session)
    key = Fernet.generate_key().decode()
    wrong_key = Fernet.generate_key().decode()
    mal_link.upsert_credentials(
        session,
        player.telegram_user_id,
        data=CredentialsData(
            encryption_key=key,
            access_token="real-access-token",
            refresh_token="real-refresh-token",
            expires_at=datetime.now(UTC).replace(tzinfo=None),
            mal_username=None,
        ),
    )
    session.commit()

    with pytest.raises(InvalidToken):
        mal_link.get_credentials(session, player.telegram_user_id, encryption_key=wrong_key)


def test_delete_credentials_removes_the_row(session: Session) -> None:
    player = _make_player(session)
    key = Fernet.generate_key().decode()
    mal_link.upsert_credentials(
        session,
        player.telegram_user_id,
        data=CredentialsData(
            encryption_key=key,
            access_token="a",
            refresh_token="b",
            expires_at=datetime.now(UTC).replace(tzinfo=None),
            mal_username=None,
        ),
    )
    session.commit()

    mal_link.delete_credentials(session, player.telegram_user_id)
    session.commit()

    assert mal_link.get_credentials(session, player.telegram_user_id, encryption_key=key) is None
