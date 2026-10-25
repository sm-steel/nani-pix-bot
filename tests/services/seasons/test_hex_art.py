from collections.abc import Iterator

import pytest
from PIL import Image

from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.seasons import hex_art


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    hex_art.art.cache_clear()
    yield
    hex_art.art.cache_clear()


@pytest.mark.parametrize("name", [*(kind.value for kind in HexKind), hex_art.OMAMORI])
def test_every_hex_kind_and_the_omamori_have_art(name: str) -> None:
    image = hex_art.art(name)

    assert image is not None
    assert image.mode == "RGBA"
    assert image.size == (512, 512)


def test_art_is_decoded_once(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(tmp_path / "kappa.png")
    monkeypatch.setattr(hex_art, "HEX_ART_DIR", tmp_path)

    assert hex_art.art("kappa") is hex_art.art("kappa")


def test_missing_art_is_none_and_logged_once(tmp_path, monkeypatch, log_records) -> None:
    monkeypatch.setattr(hex_art, "HEX_ART_DIR", tmp_path)

    assert hex_art.art("kappa") is None
    assert hex_art.art("kappa") is None
    assert [r.level for r in log_records if r.extra.get("name") == "kappa"] == ["ERROR"]
