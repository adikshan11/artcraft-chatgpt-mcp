"""Turn the generated phoenix engraving into flat art VectorCraft can trace.

The source (design/phoenix-source.png) is a 1264 px engraving generated in Canva:
dark ink lines on a yellow bird on a yellow field. This keeps the ink as black,
fills the bird's silhouette with paper white, and leaves the field pure white so
the trace can drop it:
    python3 -I design/prep_phoenix.py design/phoenix-source.png design/phoenix-flat.png
Needs Pillow, NumPy and SciPy.
"""

import sys

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

INK, PAPER = (11, 11, 12), (243, 236, 220)

src, dst = sys.argv[1], sys.argv[2]
rgb = np.asarray(Image.open(src).convert("RGB")).astype(float)
lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
ink = lum < 120
# The silhouette: close the dense linework into one shape, fill it, keep the largest piece.
bird = ndi.binary_fill_holes(ndi.binary_closing(lum < 150, structure=np.ones((3, 3)), iterations=7))
labels, n = ndi.label(bird)
bird = labels == 1 + int(np.argmax(ndi.sum(bird, labels, range(1, n + 1))))
bird = ndi.binary_opening(bird, iterations=3)
out = np.full(rgb.shape, 255, np.uint8)
out[bird] = PAPER
out[ink] = INK
Image.fromarray(out).save(dst)
