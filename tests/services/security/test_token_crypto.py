import pytest
from cryptography.fernet import Fernet, InvalidToken

from nani_pix_bot.services.security.token_crypto import decrypt, encrypt


def test_encrypt_then_decrypt_round_trips() -> None:
    key = Fernet.generate_key().decode()
    ciphertext = encrypt(key, "a-real-access-token")
    assert decrypt(key, ciphertext) == "a-real-access-token"


def test_encrypt_output_is_not_the_plaintext() -> None:
    key = Fernet.generate_key().decode()
    ciphertext = encrypt(key, "a-real-access-token")
    assert ciphertext != "a-real-access-token"


def test_decrypt_with_wrong_key_raises_invalid_token() -> None:
    key = Fernet.generate_key().decode()
    other_key = Fernet.generate_key().decode()
    ciphertext = encrypt(key, "a-real-access-token")
    with pytest.raises(InvalidToken):
        decrypt(other_key, ciphertext)


def test_decrypt_garbled_ciphertext_raises_invalid_token() -> None:
    key = Fernet.generate_key().decode()
    with pytest.raises(InvalidToken):
        decrypt(key, "not-a-real-fernet-token")
