import pytest

from nani_pix_bot.services import version


def test_installed_version_reads_the_distributions_own_metadata(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, str] = {}

    def fake_version(distribution_name: str) -> str:
        seen["name"] = distribution_name
        return "9.9.9"

    monkeypatch.setattr(version, "_installed_version", fake_version)

    assert version.installed_version() == "9.9.9"
    assert seen["name"] == "nani-pix-bot"
