#!/usr/bin/env python3
"""Image → ASCII by structural glyph matching.

Density ramps only ask "how much ink does this cell need?", which is dithering
with letters — it reproduces tone but never orientation, so diagonal artwork
comes out mushy. This instead renders every candidate character in a real
monospace font, then for each cell picks the glyph whose actual pixels best fit
that patch of the image. Edges get edge-shaped characters: `/` on a rising
stroke, `\\` on a falling one, `_` along a horizontal.

Matching is least-squares against the raw patch, with the image sampled at full
character-cell resolution so hairlines are never averaged away before the
comparison happens.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
# Full printable ASCII. Dropping the dense glyphs (@#%$&) was tempting for
# "cleanliness" and made the art markedly worse — they are what fill solid
# interiors, and without them the matcher substitutes M/W everywhere.
CANDIDATES = "".join(chr(c) for c in range(32, 127))


def build_atlas(font_path: str, px: int):
    """Render each candidate glyph into a fixed cell. Returns (chars, matrix)."""
    font = ImageFont.truetype(font_path, px)
    cw = round(font.getlength("M"))
    asc, desc = font.getmetrics()
    ch = asc + desc
    glyphs, chars = [], []
    for c in CANDIDATES:
        img = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(img).text((0, 0), c, fill=255, font=font)
        glyphs.append(np.asarray(img, dtype=np.float32).ravel() / 255.0)
        chars.append(c)
    return chars, np.stack(glyphs), cw, ch


def ink(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im).astype(np.float32) / 255.0
    alpha = a[..., 3]
    if alpha.std() > 0.01:
        return alpha
    lum = a[..., :3].mean(axis=2)
    return 1.0 - lum if lum.mean() > 0.5 else lum


def crop(m, t=0.04):
    ys, xs = np.where(m > t)
    return m if len(ys) == 0 else m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def render(path: Path, cols: int, px: int = 16, boost: float = 1.9) -> str:
    chars, atlas, cw, ch = build_atlas(FONT, px)
    m = crop(ink(path))
    h, w = m.shape
    rows = max(1, round(cols * (h / w) * cw / ch))

    # Sample at full cell resolution — the comparison needs the real stroke.
    big = np.asarray(
        Image.fromarray((m * 255).astype(np.uint8))
             .resize((cols * cw, rows * ch), Image.LANCZOS)
    ).astype(np.float32) / 255.0
    big = np.clip(big * boost, 0, 1)      # thin strokes are faint; lift them

    patches = (big.reshape(rows, ch, cols, cw)
                  .transpose(0, 2, 1, 3)
                  .reshape(rows * cols, ch * cw))

    # argmin ||p - g||^2  ==  argmin (||g||^2 - 2 p·g); ||p||^2 is constant per cell.
    gn = (atlas ** 2).sum(1)
    best = np.argmin(gn[None, :] - 2.0 * (patches @ atlas.T), axis=1)

    out = np.array(chars)[best].reshape(rows, cols)
    return "\n".join("".join(r).rstrip() for r in out)


if __name__ == "__main__":
    src = Path(sys.argv[1])
    for cols in (int(x) for x in (sys.argv[2:] or ["72"])):
        print(f"\n===== glyph-matched · {cols} cols =====")
        print(render(src, cols))
