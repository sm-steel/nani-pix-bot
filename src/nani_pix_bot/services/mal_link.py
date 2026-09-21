"""Per-player MAL OAuth linking state — the DB-facing half of
docs/superpowers/specs/2026-09-21-mal-account-linking-design.md.
Encrypts MalCredentials.access_token/refresh_token on write and
decrypts on read (see services/security/token_crypto.py) so no other
module ever touches those two columns' raw ciphertext directly.

Mirrors services/players.py's plain-session-parameter style — callers
own the session_scope(...)/commit, these functions just mutate."""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.mal_link import MalCredentials, PendingMalLink
from nani_pix_bot.services.security import token_crypto


@dataclass(frozen=True)
class CredentialsData:
    """All token/auth info needed to update MalCredentials — bundled into
    one object so `upsert_credentials` doesn't need a 6-argument
    signature (a qlty 'many parameters' smell), following the pattern
    used elsewhere in the codebase (e.g. dm_start/keyboards.py)."""

    encryption_key: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    mal_username: str | None


def upsert_pending_link(
    session: Session, telegram_user_id: int, *, state: str, code_verifier: str
) -> PendingMalLink:
    """Create or overwrite `telegram_user_id`'s in-flight /linkmal
    attempt. A second /linkmal while one is already pending silently
    restarts it — see the spec's confirmed re-link UX."""
    pending = session.get(PendingMalLink, telegram_user_id)
    if pending is None:
        pending = PendingMalLink(
            telegram_user_id=telegram_user_id,
            state=state,
            code_verifier=code_verifier,
            created_at=datetime.now(UTC),
        )
        session.add(pending)
    else:
        pending.state = state
        pending.code_verifier = code_verifier
        pending.created_at = datetime.now(UTC)
    return pending


def get_pending_link(session: Session, telegram_user_id: int) -> PendingMalLink | None:
    return session.get(PendingMalLink, telegram_user_id)


def delete_pending_link(session: Session, telegram_user_id: int) -> None:
    pending = session.get(PendingMalLink, telegram_user_id)
    if pending is not None:
        session.delete(pending)


@dataclass(frozen=True)
class DecryptedCredentials:
    access_token: str
    refresh_token: str
    expires_at: datetime
    mal_username: str | None


def upsert_credentials(
    session: Session,
    telegram_user_id: int,
    *,
    data: CredentialsData,
) -> None:
    """Encrypts `data.access_token`/`data.refresh_token` before writing —
    callers always pass the real plaintext tokens, never pre-encrypted values."""
    encrypted_access = token_crypto.encrypt(data.encryption_key, data.access_token)
    encrypted_refresh = token_crypto.encrypt(data.encryption_key, data.refresh_token)

    credentials = session.get(MalCredentials, telegram_user_id)
    if credentials is None:
        credentials = MalCredentials(
            telegram_user_id=telegram_user_id,
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            expires_at=data.expires_at,
            mal_username=data.mal_username,
            linked_at=datetime.now(UTC),
        )
        session.add(credentials)
        logger.info("Player {} linked their MAL account", telegram_user_id)
    else:
        credentials.access_token = encrypted_access
        credentials.refresh_token = encrypted_refresh
        credentials.expires_at = data.expires_at
        credentials.mal_username = data.mal_username
        logger.info("Player {} re-linked their MAL account", telegram_user_id)


def get_credentials(
    session: Session, telegram_user_id: int, *, encryption_key: str
) -> DecryptedCredentials | None:
    """Decrypted credentials, or None if never linked. Raises
    `cryptography.fernet.InvalidToken` (propagated, not caught here) if
    `encryption_key` doesn't match what these tokens were encrypted
    with — the caller must treat that the same as an unlinked player,
    not a crash (see the spec's key-rotation note)."""
    credentials = session.get(MalCredentials, telegram_user_id)
    if credentials is None:
        return None
    return DecryptedCredentials(
        access_token=token_crypto.decrypt(encryption_key, credentials.access_token),
        refresh_token=token_crypto.decrypt(encryption_key, credentials.refresh_token),
        expires_at=credentials.expires_at,
        mal_username=credentials.mal_username,
    )


def delete_credentials(session: Session, telegram_user_id: int) -> None:
    credentials = session.get(MalCredentials, telegram_user_id)
    if credentials is not None:
        session.delete(credentials)
        logger.info("Player {} unlinked their MAL account", telegram_user_id)
