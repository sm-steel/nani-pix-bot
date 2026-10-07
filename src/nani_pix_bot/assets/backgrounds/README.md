# Card backgrounds

Optional images drawn behind the achievement and podium cards. Drop PNG, JPG
or WEBP files into these folders (any size; they are cover-cropped to
1200x630):

- `unlock/bronze/`, `unlock/silver/`, `unlock/gold/`, `unlock/platinum/`
- `podium/week/`, `podium/month/`, `podium/year/`

Several files per folder are fine: one is picked per player (by name order,
deterministically). A folder that is missing or empty falls back to the flat
dark style.

Generation prompts and the style guide for these images: `docs/card-backgrounds.md`.
