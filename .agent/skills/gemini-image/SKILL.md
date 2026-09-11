---
name: gemini-image
description: Generate images through the Google Gemini and Imagen APIs. Use for posters, thumbnails, illustrations and any image asset, including images that must carry readable text. Two backends, one for true aspect-ratio control and one for best quality and text rendering.
---

# gemini-image

Generates image files from a text prompt.

## Two backends, and when each one wins

| Model | Endpoint | Use it for |
| :--- | :--- | :--- |
| `gemini-3-pro-image` (default) | `:generateContent` | best overall quality, and any image with readable text in it |
| `gemini-2.5-flash-image` | `:generateContent` | same family, cheaper and faster |
| `imagen-4.0-generate-001` | `:predict` | clean poster generation with true aspect-ratio control |

Aspect ratio is honored by the Imagen backend only: `1:1`, `3:4`, `4:3`, `9:16`, `16:9`.

## Auth

`GEMINI_API_KEY`, read from the environment first, then from `token.env` next to
the script. The token file is gitignored and never syncs to the public template.

## Usage

```bash
python3 .agent/skills/gemini-image/generate.py \
    --prompt "..." --out path.png

python3 .agent/skills/gemini-image/generate.py \
    --prompt "..." --out poster.png --model imagen-4.0-generate-001 --aspect 3:4

python3 .agent/skills/gemini-image/generate.py \
    --prompt-file prompt.txt --out path.png --model gemini-3-pro-image
```

`--out` is required. `--n` asks for more than one image.

## Cost

Image generation is metered, so it is one of the three things that are never
cheap to undo. Ask before a batch run. A single image for a draft does not need
a question.

## Handing the image over

An image is a local file, so it goes to the owner as a link for this machine:

```bash
bash .agent/scripts/artifact_link.sh <path>
```

Never hand over a bare relative path, and never hardcode the `\\wsl.localhost`
form, which a Mac cannot open.
