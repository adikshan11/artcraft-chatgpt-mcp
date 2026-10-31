"""Draw the BridgeCraft logo in VectorCraft and export it.

A phoenix rising, wings raised in a V, its tail turning to flame as it runs off
the bottom of the tile. It fills the tile the way the animals fill the Craft app
icons, and it is nothing those icons already use (VectorCraft has the dragon and
SoundCraft a songbird). Engraving style like those icons: ink linework on paper
white, on a single colour field with sun rays.

The bird is an engraving generated in Canva (design/phoenix-source.jpg), flattened
by design/prep_phoenix.py to design/phoenix-flat.png and traced here with
VectorCraft's Image Trace, so the logo is vector paths.

Run inside the Craft sandbox with VectorCraft's CLI in the image, this folder at
/design and an output folder at /out:
    python3 -I design/draw_logo.py
It writes bridgecraft.svg, bridgecraft.vectorcraft and preview.png to /out.
"""

import base64
import json
import math
import os
import subprocess
import sys

sys.path.insert(0, "/test")
import mcp_driver  # noqa: E402  (the stdio MCP client from the sandbox test folder)

OUT = "/out"
ART = os.environ.get("PHOENIX_ART", "/design/phoenix-flat.png")
CLI = os.environ.get("VECTORCRAFT_CLI", "/opt/craft/vectorcraft/vectorcraft-cli")
FIELD, FIELD_LIGHT = "#ffc62e", "#ffd866"
TILE = ("M112 0 L400 0 C461.9 0 512 50.1 512 112 L512 400 C512 461.9 461.9 512 400 512 "
        "L112 512 C50.1 512 0 461.9 0 400 L0 112 C0 50.1 50.1 0 112 0 Z")

m = mcp_driver.Mcp([CLI, "mcp", "--headless"])


def run(tool, args):
    r = m.call(tool, args)
    if "error" in r or r["result"].get("isError"):
        raise SystemExit(f"{tool} failed: {json.dumps(r)[:400]}")
    return r["result"]


def command(name, params=None):
    res = run("run_command", {"command": name, "params": params or {}})
    text = "".join(c.get("text", "") for c in res["content"] if c.get("type") == "text")
    try:
        return json.loads(text)
    except ValueError:
        return text


command("file.new", {"width": 512, "height": 512})
# Field, and sun rays fanning out from behind the bird's chest.
run("draw_path", {"d": TILE, "fill": FIELD, "stroke": "none"})
src = (256, 250)
for k in range(36):
    a, w = math.radians(k * 10 + 5), math.radians(2.2)
    r0, r1 = 60, 380
    p = [(src[0] + r0 * math.cos(a), src[1] + r0 * math.sin(a)),
         (src[0] + r1 * math.cos(a - w), src[1] + r1 * math.sin(a - w)),
         (src[0] + r1 * math.cos(a + w), src[1] + r1 * math.sin(a + w))]
    run("draw_path", {"d": "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in p) + " Z", "fill": FIELD_LIGHT, "stroke": "none"})
# The bird: place the flattened engraving over the whole tile and trace it.
placed = command("file.place", {"path": ART, "link": False, "rect": [0, 0, 512, 512]})
traced = command("imageTrace.makeAndExpand", {"id": placed["ids"][0], "params": {
    "mode": "color", "palette": "limited", "colors": 3, "paths": 70, "corners": 60, "noise": 5,
    "method": "overlapping", "ignoreWhite": True}})
print("trace:", json.dumps({k: traced[k] for k in ("paths", "anchors", "colors") if k in traced}))
# Clip everything to the rounded square: the topmost object becomes the mask.
run("draw_path", {"d": TILE, "fill": "none", "stroke": "none"})
command("select.all")
command("object.clippingMask.make")
res = run("render_preview", {"max_side": 1024})
for c in res["content"]:
    if c.get("type") == "image":
        open(f"{OUT}/preview.png", "wb").write(base64.b64decode(c["data"]))
run("save_file", {"path": f"{OUT}/bridgecraft.vectorcraft"})
m.close()
# One decimal place is plenty at this size and keeps the SVG a quarter smaller.
subprocess.run([CLI, "convert", f"{OUT}/bridgecraft.vectorcraft", f"{OUT}/bridgecraft.svg", "--decimals", "1"],
               check=True, stdout=subprocess.DEVNULL)
