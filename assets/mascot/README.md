# Nani Pix — character files

Nani Pix is the game's host and mascot: a blue-haired anime pixel mage whose spell
turns the world into pixels. These are her official character files, kept
for making new art of her.

| File | What it is |
|---|---|
| `main_reference.png` | Full body, transparent background. The main reference. |
| `reference_sheet.png` | Front, side and back views, four expressions, palette and details. |
| `poses/cast.webp` | Winking, casting pixels. Stage 1 and the start of the reveal video. |
| `poses/nope.webp` | Teasing "nope" with a pixel ✗. The stages in between. |
| `poses/nani.webp` | Shocked "nani?!". The last stage, and a HARD MODE vote. |
| `poses/win.webp` | Cheering. A won round's reveal. |
| `poses/oops.webp` | Sheepish. An unsolved round's reveal. |
| `poses/sly.webp` | Scheming, tossing a pixel crown. The first stage of a game she took by overthrow. |

The poses are full-size originals (lossless WebP). In the game she uses small
copies in `src/nani_pix_bot/assets/mascot/`.

## Making new art

Attach `main_reference.png` and `reference_sheet.png` to the image generator
as character references, and describe her as: long electric-blue hair fading
to cyan at the ends, where the tips break into floating square pixels; one
big ahoge; magenta eyes with square catchlights; a large navy witch hat with
a bent tip, a cyan 8-bit pixel-pattern band on the brim and a blue
diamond-shaped crystal pin; a navy capelet with the same trim and a crystal
brooch; white blouse, navy pleated skirt, dark tights, navy ankle boots; a
short wand tipped with a blue diamond crystal.

## Costumes

Her costumes are the same six poses in other outfits. Each is a 384 px wide
RGBA PNG, at most 480 px tall, in
`src/nani_pix_bot/assets/mascot/costumes/<key>/<pose>.png`, with the
full-size lossless WebP original in `assets/mascot/costumes/<key>/<pose>.webp`.
A costume needs all six poses. The keys are:

| Key | Costume |
|---|---|
| `pumpkin` | The Halloween season's default |
| `kitsune` | Fox spirit |
| `bakeneko` | Cat spirit |
| `chochin` | Paper lantern |
| `pastel_witch` | Pastel witch |
| `skeleton` | Skeleton |

For new costume art, attach only `main_reference.png` and
`reference_sheet.png` to the generator and describe the pose in the prompt.

## License

MIT, like the code: see [LICENSE](../../LICENSE).
