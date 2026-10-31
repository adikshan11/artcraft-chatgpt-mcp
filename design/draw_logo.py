"""Draw the BridgeCraft logo in VectorCraft and export it.

An Arctic tern, head-on, its raised wings making an arch over the water: the
bird is the bridge (the tern also flies pole to pole every year). Engraving
style like the Craft app icons: ink linework and hatching on a single colour field.

Run inside the Craft sandbox with VectorCraft's CLI at /opt/craft and an
output folder at /out:
    python3 -I design/draw_logo.py
It writes bridgecraft.svg, bridgecraft.vectorcraft and preview.png to /out.
"""

import base64
import json
import math
import sys

sys.path.insert(0, "/test")
import mcp_driver  # noqa: E402  (the stdio MCP client from the sandbox test folder)

OUT = "/out"
INK, PAPER, FIELD = "#0b0b0c", "#f3ecdc", "#ffc62e"
FIELD_DARK, FIELD_LIGHT = "#e9a20f", "#ffd866"
BEAK, BEAK_DARK = "#e8432a", "#a92a17"
C = (256.0, 256.0)
SAFE_R = 246.0  # background art stays inside this circle, so inside the rounded square

paths = []  # (d, paint) in drawing order


def add(d, **paint):
    paths.append((d, paint))


def fmt(p):
    return f"{p[0]:.1f} {p[1]:.1f}"


def bez(p, t):
    u = 1 - t
    return (
        u**3 * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t**3 * p[3][0],
        u**3 * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t**3 * p[3][1],
    )


def tangent(p, t, h=1e-3):
    a, b = bez(p, max(0, t - h)), bez(p, min(1, t + h))
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return dx / n, dy / n


def add_v(a, b, k=1.0):
    return a[0] + b[0] * k, a[1] + b[1] * k


def rot(v, deg):
    r = math.radians(deg)
    return v[0] * math.cos(r) - v[1] * math.sin(r), v[0] * math.sin(r) + v[1] * math.cos(r)


def in_safe(p):
    return math.hypot(p[0] - C[0], p[1] - C[1]) <= SAFE_R


# ---------------------------------------------------------------- background
def background():
    add("M112 0 L400 0 C461.9 0 512 50.1 512 112 L512 400 C512 461.9 461.9 512 400 512 "
        "L112 512 C50.1 512 0 461.9 0 400 L0 112 C0 50.1 50.1 0 112 0 Z", fill=FIELD, stroke="none")
    # Sun rays fanning out behind the head.
    src = (256, 150)
    for k in range(36):
        a = math.radians(k * 10 + 5)
        r0, r1 = 70, 300
        while r1 > r0 and not in_safe((src[0] + r1 * math.cos(a), src[1] + r1 * math.sin(a))):
            r1 -= 2
        w = math.radians(2.2)
        p1 = (src[0] + r0 * math.cos(a), src[1] + r0 * math.sin(a))
        p2 = (src[0] + r1 * math.cos(a - w), src[1] + r1 * math.sin(a - w))
        p3 = (src[0] + r1 * math.cos(a + w), src[1] + r1 * math.sin(a + w))
        add(f"M{fmt(p1)} L{fmt(p2)} L{fmt(p3)} Z", fill=FIELD_LIGHT, stroke="none")
    # Engraved water under the arch: the gap the bird spans.
    for row, y in enumerate(range(396, 504, 11)):
        pts = [(x, y + 4 * math.sin((x + row * 17) / 13.0)) for x in range(10, 503, 4)]
        pts = [p for p in pts if in_safe(p)]
        if len(pts) < 3:
            continue
        d = f"M{fmt(pts[0])}" + "".join(f" L{fmt(p)}" for p in pts[1:])
        add(d, fill="none", stroke=FIELD_DARK, strokeWidth=3.2 if row % 2 else 2.2, cap="round")


# ---------------------------------------------------------------- feathers
def feather(base, u, length, width, hatch_side=1, hatch=4, weight=2.6):
    """One feather: outline, quill and engraved hatching along one side."""
    v = (-u[1], u[0])
    tip = add_v(base, u, length)
    l0 = add_v(base, v, -width / 2)
    r0 = add_v(base, v, width / 2)
    d = (f"M{fmt(l0)} C{fmt(add_v(l0, u, length * 0.72))} {fmt(add_v(tip, v, -width * 0.42))} {fmt(tip)} "
         f"C{fmt(add_v(tip, v, width * 0.42))} {fmt(add_v(r0, u, length * 0.72))} {fmt(r0)} Z")
    add(d, fill=PAPER, stroke=INK, strokeWidth=weight, join="round")
    q0 = add_v(add_v(base, u, length * 0.06), v, -width * 0.08 * hatch_side)
    q1 = add_v(add_v(base, u, length * 0.9), v, -width * 0.04 * hatch_side)
    add(f"M{fmt(q0)} L{fmt(q1)}", fill="none", stroke=INK, strokeWidth=max(1.2, weight * 0.6), cap="round")
    for i in range(hatch):
        f = 0.14 + 0.07 * i
        off = width * f * hatch_side
        s = add_v(add_v(base, u, length * (0.18 + 0.03 * i)), v, off)
        e = add_v(add_v(base, u, length * (0.8 - 0.09 * i)), v, off * 0.9)
        add(f"M{fmt(s)} L{fmt(e)}", fill="none", stroke=INK, strokeWidth=1.15, cap="round")


def wing(side):
    s = 1 if side == "R" else -1
    X = lambda p: (C[0] + s * (p[0] - C[0]), p[1])  # noqa: E731
    lead = [(262, 170), (352, 108), (468, 150), (494, 418)]  # leading edge, shoulder to tip
    centre = (256, 420)

    def frame(t):
        p = bez(lead, t)
        tx, ty = tangent(lead, t)
        n = (-ty, tx)
        if (centre[0] - p[0]) * n[0] + (centre[1] - p[1]) * n[1] < 0:
            n = (-n[0], -n[1])
        return p, (tx, ty), n

    # Flight feathers (back): secondaries near the body, long primaries at the tip.
    feathers = []
    steps = 22
    for k in range(steps):
        t = 0.08 + 0.9 * k / (steps - 1)
        p, tng, n = frame(t)
        sweep = 18 + 46 * t**1.6          # primaries rake out toward the tip
        u = rot(n, -sweep * 1)            # rotate the normal toward the tip direction
        if (u[0] * tng[0] + u[1] * tng[1]) < 0:
            u = rot(n, sweep)
        length = 52 + 96 * t**1.7
        base = add_v(p, n, 26)
        feathers.append((base, u, length, 25 + 7 * t))
    for base, u, length, width in reversed(feathers):
        b, uu = X(base), (s * u[0], u[1])
        feather(b, uu, length, width, hatch_side=s, hatch=4, weight=2.6)

    # Wing body: the leading edge band the feathers hang from.
    inner = [add_v(bez(lead, t), frame(t)[2], 34 - 8 * t) for t in [k / 24 for k in range(25)]]
    outer = [bez(lead, t) for t in [k / 24 for k in range(25)]]
    d = f"M{fmt(X(outer[0]))}" + "".join(f" L{fmt(X(p))}" for p in outer[1:])
    d += "".join(f" L{fmt(X(p))}" for p in reversed(inner)) + " Z"
    add(d, fill=PAPER, stroke=INK, strokeWidth=4, join="round")

    # Coverts: rows of small scale feathers over the band.
    for row, (off, size, count) in enumerate([(14, 15, 26)]):
        for k in range(count):
            t = 0.04 + 0.9 * (k + 0.5 * row) / count
            p, tng, n = frame(t)
            base = add_v(p, n, off - 6)
            u = rot(n, -12 * s if s > 0 else 12)
            feather(X(base), (s * u[0], u[1]), size, size * 1.15, hatch_side=s, hatch=2, weight=1.6)

    # Engraved shading lines along the leading edge.
    for j in range(3):
        pts = [add_v(bez(lead, t), frame(t)[2], 3 + j * 3.2) for t in [0.06 + k * 0.88 / 30 for k in range(31)]]
        d = f"M{fmt(X(pts[0]))}" + "".join(f" L{fmt(X(p))}" for p in pts[1:])
        add(d, fill="none", stroke=INK, strokeWidth=1.1, cap="round")


# ---------------------------------------------------------------- body, tail, head
def tail():
    for s in (-1, 1):
        base = (256 + 8 * s, 318)
        tipdir = (0.36 * s, 1.0)
        n = math.hypot(*tipdir)
        feather(base, (tipdir[0] / n, tipdir[1] / n), 132, 22, hatch_side=s, hatch=3, weight=2.6)
    feather((256, 318), (0.0, 1.0), 62, 30, hatch_side=1, hatch=3, weight=2.6)


def body():
    top, bottom = 150, 352

    def half(y):
        f = (y - top) / (bottom - top)
        return 48 * math.sin(math.pi * min(1, max(0, f)) ** 0.85) ** 0.9

    left = [(256 - half(y), y) for y in range(top, bottom + 1, 6)]
    right = [(256 + half(y), y) for y in range(bottom, top - 1, -6)]
    d = f"M{fmt(left[0])}" + "".join(f" L{fmt(p)}" for p in left[1:] + right) + " Z"
    add(d, fill=PAPER, stroke=INK, strokeWidth=4, join="round")
    # Contour hatching: heavier on the right (shadow side), light on the left.
    for y in range(196, 346, 6):
        w = half(y) * 0.92
        if w < 8:
            continue
        a, b = (256 + w * 0.18, y), (256 + w, y - 3)
        add(f"M{fmt(a)} Q{fmt((256 + w * 0.6, y + 5))} {fmt(b)}", fill="none", stroke=INK, strokeWidth=1.25, cap="round")
        if y % 12 == 0:
            a, b = (256 - w, y - 3), (256 - w * 0.62, y + 1)
            add(f"M{fmt(a)} Q{fmt((256 - w * 0.82, y + 3))} {fmt(b)}", fill="none", stroke=INK, strokeWidth=1.0, cap="round")


def head():
    cx, cy, r = 256, 140, 44
    add(f"M{cx - r} {cy} A{r} {r} 0 1 1 {cx + r} {cy} A{r} {r} 0 1 1 {cx - r} {cy} Z", fill=PAPER, stroke=INK, strokeWidth=4)
    # Cheek and throat shading.
    for k in range(5):
        y = cy + 14 + k * 5
        add(f"M{cx + 18} {y} Q{cx + 30} {y + 2} {cx + 38 - k * 3} {y - 4}", fill="none", stroke=INK, strokeWidth=1.1, cap="round")
        add(f"M{cx - 38 + k * 3} {y - 4} Q{cx - 32} {y + 1} {cx - 24} {y}", fill="none", stroke=INK, strokeWidth=0.9, cap="round")
    # The black cap, down to the eyes, with engraved sheen.
    add(f"M{cx - r - 1} {cy + 4} A{r + 1} {r + 1} 0 0 1 {cx + r + 1} {cy + 4} "
        f"C{cx + 34} {cy + 18} {cx + 22} {cy + 12} {cx} {cy + 12} C{cx - 22} {cy + 12} {cx - 34} {cy + 18} {cx - r - 1} {cy + 4} Z",
        fill=INK, stroke=INK, strokeWidth=3, join="round")
    for k in range(6):
        rr = r - 8 - k * 4.5
        add(f"M{cx - rr * 0.95:.1f} {cy - rr * 0.32:.1f} A{rr:.1f} {rr:.1f} 0 0 1 {cx - rr * 0.1:.1f} {cy - rr:.1f}",
            fill="none", stroke=PAPER, strokeWidth=1.2 if k % 2 else 0.8, cap="round")
    for ex in (cx - 19, cx + 19):
        ey = cy + 6
        add(f"M{ex - 8} {ey} A8 8 0 1 1 {ex + 8} {ey} A8 8 0 1 1 {ex - 8} {ey} Z", fill=PAPER, stroke="none")
        add(f"M{ex - 5.5} {ey} A5.5 5.5 0 1 1 {ex + 5.5} {ey} A5.5 5.5 0 1 1 {ex - 5.5} {ey} Z", fill=INK, stroke="none")
        add(f"M{ex - 3.5} {ey - 2} A1.8 1.8 0 1 1 {ex - 0} {ey - 2} A1.8 1.8 0 1 1 {ex - 3.5} {ey - 2} Z", fill=PAPER, stroke="none")
    # Beak, pointing down at the viewer.
    add(f"M{cx - 13} {cy + 26} C{cx - 10} {cy + 46} {cx - 3} {cy + 72} {cx} {cy + 84} "
        f"C{cx + 3} {cy + 72} {cx + 10} {cy + 46} {cx + 13} {cy + 26} C{cx + 5} {cy + 22} {cx - 5} {cy + 22} {cx - 13} {cy + 26} Z",
        fill=BEAK, stroke=INK, strokeWidth=3, join="round")
    for k in range(4):
        x = cx + 3 + k * 2.4
        add(f"M{x:.1f} {cy + 30} L{x - 1.5:.1f} {cy + 70 - k * 9}", fill="none", stroke=BEAK_DARK, strokeWidth=1.1, cap="round")
    add(f"M{cx} {cy + 28} L{cx} {cy + 80}", fill="none", stroke=INK, strokeWidth=1.3, cap="round")


background()
wing("L")
wing("R")
tail()
body()
head()

m = mcp_driver.Mcp(["/opt/craft/vectorcraft-cli", "mcp", "--headless"])
m.init()


def run(tool, args):
    r = m.call(tool, args)
    if "error" in r or r["result"].get("isError"):
        raise SystemExit(f"{tool} failed: {json.dumps(r)[:400]}")
    return r["result"]


run("run_command", {"command": "file.new", "params": {"width": 512, "height": 512}})
for d, paint in paths:
    args = {"d": d, **paint}
    if args.get("stroke") not in (None, "none"):
        args.setdefault("join", "round")
    run("draw_path", args)
# Clip everything to the rounded square: the topmost object becomes the mask.
run("draw_path", {"d": paths[0][0], "fill": "none", "stroke": "none"})
run("run_command", {"command": "select.all"})
run("run_command", {"command": "object.clippingMask.make"})
res = run("render_preview", {"max_side": 1024})
for c in res["content"]:
    if c.get("type") == "image":
        open(f"{OUT}/preview.png", "wb").write(base64.b64decode(c["data"]))
run("export", {"path": f"{OUT}/bridgecraft.svg", "format": "svg"})
run("save_file", {"path": f"{OUT}/bridgecraft.vectorcraft"})
m.close()
print(len(paths), "paths")
