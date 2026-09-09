import pytest

from nani_pix_bot import config


def test_database_url_reads_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    assert config.database_url() == "sqlite:///:memory:"


def test_database_url_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.database_url()
