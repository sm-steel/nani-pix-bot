"""Render the user's guide's pixelation-stage images (issue #359).

Runs the bot's own `pixelate()` with the default stage widths and the
default algorithm on a project-owned card background, so the guide shows
exactly what a round looks like. Called by `docs/site`'s predev/prebuild
npm scripts; the output directory is gitignored.

Usage: uv run python scripts/gen_docs_stages.py
"""

import io
import json
from pathlib import Path

from PIL import Image

from nani_pix_bot.models.enums import DEFAULT_ALGORITHM
from nani_pix_bot.services.pixelate import pixelate
from nani_pix_bot.services.settings.stage_config import DEFAULT_STAGE_CONFIG

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "src" / "nani_pix_bot" / "assets" / "backgrounds" / "unlock" / "gold" / "1.png"
OUT_DIR = ROOT / "docs" / "site" / "src" / "assets" / "generated"


def _save_webp(image: Image.Image, path: Path, *, lossless: bool) -> None:
    image.convert("RGB").save(path, format="WEBP", lossless=lossless, quality=85)


def generate(sample: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    source = sample.read_bytes()
    table = []
    for number, settings in enumerate(DEFAULT_STAGE_CONFIG.values(), start=1):
        png = pixelate(source, settings.target_width, DEFAULT_ALGORITHM)
        with Image.open(io.BytesIO(png)) as stage:
            # Lossless: lossy WebP smears the block edges the page shows off.
            _save_webp(stage, out_dir / f"stage-{number}.webp", lossless=True)
        table.append(
            {
                "stage": number,
                "width": settings.target_width,
                "wrongGuessLimit": settings.wrong_guess_limit,
            }
        )
    with Image.open(sample) as original:
        _save_webp(original, out_dir / "original.webp", lossless=False)
    (out_dir / "stages.json").write_text(
        json.dumps(table, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    generate(SAMPLE, OUT_DIR)
    print(f"wrote {len(DEFAULT_STAGE_CONFIG)} stage images to {OUT_DIR}")
