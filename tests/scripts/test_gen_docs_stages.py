"""The guide's stage images must be exactly what the bot itself would post."""

import io
import json
from pathlib import Path

from PIL import Image

from nani_pix_bot.models.enums import DEFAULT_ALGORITHM
from nani_pix_bot.services.pixelate import pixelate
from nani_pix_bot.services.settings.stage_config import DEFAULT_STAGE_CONFIG
from scripts import gen_docs_stages


def _rgb_bytes(image: Image.Image) -> bytes:
    return image.convert("RGB").tobytes()


def test_each_stage_is_the_bots_own_pixelation(tmp_path: Path) -> None:
    gen_docs_stages.generate(gen_docs_stages.SAMPLE, tmp_path)
    source = gen_docs_stages.SAMPLE.read_bytes()
    for number, settings in enumerate(DEFAULT_STAGE_CONFIG.values(), start=1):
        png = pixelate(source, settings.target_width, DEFAULT_ALGORITHM)
        expected = Image.open(io.BytesIO(png))
        with Image.open(tmp_path / f"stage-{number}.webp") as written:
            assert _rgb_bytes(written) == _rgb_bytes(expected), f"stage {number}"


def test_stage_table_matches_the_defaults(tmp_path: Path) -> None:
    gen_docs_stages.generate(gen_docs_stages.SAMPLE, tmp_path)
    table = json.loads((tmp_path / "stages.json").read_text(encoding="utf-8"))
    assert table == [
        {"stage": n, "width": s.target_width, "wrongGuessLimit": s.wrong_guess_limit}
        for n, s in enumerate(DEFAULT_STAGE_CONFIG.values(), start=1)
    ]


def test_writes_the_sharp_original_at_full_size(tmp_path: Path) -> None:
    gen_docs_stages.generate(gen_docs_stages.SAMPLE, tmp_path)
    with (
        Image.open(gen_docs_stages.SAMPLE) as sample,
        Image.open(tmp_path / "original.webp") as original,
    ):
        assert original.size == sample.size


def test_creates_missing_output_dir_and_overwrites_on_rerun(tmp_path: Path) -> None:
    out_dir = tmp_path / "nested" / "generated"
    gen_docs_stages.generate(gen_docs_stages.SAMPLE, out_dir)
    gen_docs_stages.generate(gen_docs_stages.SAMPLE, out_dir)
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "original.webp",
        "stage-1.webp",
        "stage-2.webp",
        "stage-3.webp",
        "stage-4.webp",
        "stage-5.webp",
        "stages.json",
    ]
