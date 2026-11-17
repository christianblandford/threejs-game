"""
Trees, shrubs, hedges and scanned plants for level scripts.

    sys.path.insert(0, os.path.dirname(__file__))
    from plants import broadleaf_tree, conifer_tree, bush, hedge_segment, import_model

    oak = broadleaf_tree("oak", seed=3)
    place(oak.mesh, x, y, collision="cylinder", collision_radius=oak.trunk_radius, far_mesh=oak.far.name)

    broadleaf_tree(variant, seed)   "oak" spreading, "ash" tall and narrow, "thorn" small and crooked; 6 to 11 m
    conifer_tree(variant, seed)     "spruce" broad with drooping tiers, "fir" narrow and upswept; 7.5 to 14 m
    bush(variant, seed)             "round", "tall", "low"; 0.6 to 1.8 m
    hedge_segment(length, height)   a run of clipped hedge along X
    import_model(id, max_triangles, variant)   a scanned plant from maps/models/

Variants can be given by name or by number. Every function returns a `Plant`:
`mesh` (the full plant), `far` (a stand-in mesh for the distance, see below;
its name is what the exporter's `far_mesh` property wants), `height`,
`trunk_radius`, `crown_radius`, `triangles`, `far_triangles`. Fields read as
attributes or as dictionary keys. The same arguments always give the same
plant, and asking twice in one session gives the same mesh back, so it can be
placed as linked duplicates.

How the plants are made (the usual game technique)
--------------------------------------------------
- Wood is real geometry: tapered, crooked tubes, trunk -> limbs -> branches,
  with bark UVs running along the limb at the bark texture's true scale.
- Foliage is alpha cut-out cards at the ends of the branches. Each card shows a
  whole leafy twig, painted here with numpy into a 1024 px atlas of four
  different sprays (so one spray is 512 px), with a matching normal map. The
  images are packed into the .blend. One atlas per kind of leaf: `LeavesOak`,
  `LeavesAsh`, `LeavesThorn`, `NeedlesSpruce`. Bushes and hedges reuse the
  tree atlases at a smaller card size. Most cards lie on the surface of the
  crown facing out of it; the rest stand out from it as crossed pairs.
- The leaf materials export as glTF `alphaMode: "MASK"` with the cutoff
  ALPHA_CUTOFF, double-sided. Materials are found by name before being made,
  so every plant in a file shares them; `Bark` is the level's own if the level
  script made one first.

The distant stand-in, `far`
---------------------------
- For trees and bushes it is an impostor: three cards (two for a bush)
  crossed on the trunk and fanned evenly, each showing the same picture of
  that very plant, cut out along its outline. 6 or 4 triangles, standing on
  z = 0, as wide and as tall as the plant. They are upright for an upright
  plant and lean with a leaning one: the top edge of each is set over to
  where the crown is, and the picture is of the plant stood up straight.
- The picture is a side view of the full mesh from its broadest side,
  rendered with Eevee in a scene of its own (made and removed inside the
  call; the caller's scene is not touched; about half a second a plant). It
  holds the plant's own colours with a soft shading from above only, no sun
  and no shine, because the game lights the card again. Its opaque pixels
  average IMPOSTOR_GAIN x the average colour of the plant's leaf atlas.
- All pictures share one packed atlas, `PlantCards_color` (2048 px, straight
  alpha, leaf colour carried out under the transparent pixels), one tile per
  plant in the order they were made: 264 px on its longer side for a tree,
  104 for a bush, 4 px of margin included. Its bottom left 64 px are opaque
  white. Room for 50 to 70 trees and their bushes.
- All cards share one material, `PlantCards`: the atlas as base colour, glTF
  `alphaMode: "MASK"` at ALPHA_CUTOFF, double-sided, no normal map. Custom
  properties: `impostor` (true), `alpha_cutoff`, and `white_uv`, the middle of
  the white tile, for other geometry merged into the same material.
- A card mesh has one UV map (`UVMap`), no colour attribute, and custom
  normals that lean out from the trunk and mostly up, the same on both
  faces, so the cards shade as one rounded crown; as with foliage, the game
  must not flip them on back faces.
- If no picture can be had (no Eevee, no graphics, the atlas full), or with
  IMPOSTORS = False, the stand-in is the plain shape used before: a trunk and
  a leaf-coloured lump of at most 150 triangles, opaque, in flat colours. One
  line is printed to say so. Hedges and scanned plants always get a plain
  stand-in.

What each full mesh carries for the game
----------------------------------------
- UV map 1, `UVMap`: texture coordinates.
- UV map 2, `wind` (TEXCOORD_1): u = how much the vertex sways, 0 at the foot
  of the trunk, rising along trunk and limbs, 1 at the tips of the leaf cards;
  v = a random phase 0..1 per limb, shared by everything growing from it.
- Custom normals: wood is smooth; foliage normals point outward from the middle
  of the crown and a little upward, so a crown shades as one soft volume. For
  that to hold from both sides the game must NOT flip normals on back faces
  (the game gives foliage materials no anisotropic filtering and a matte finish: src/engine/materials.ts). Faces are
  wound to agree with their normals all the same.
- Colour attribute `Col` on foliage (COLOR_0): darker inside the crown and low
  down, plus a little tone and hue variation from card to card. The leaf
  materials multiply it into the texture. Stand-ins have none; the plain ones
  do carry the `wind` map.
- Custom properties on the foliage materials: `foliage` (true),
  `alpha_cutoff`, `card_metres`.

Budgets: trees at most 2,500 triangles, bushes 400, plain stand-ins 150.
"""

import json
import math
import os
import random

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

try:
    from surfaces import surface
except ImportError:  # used on its own, without the texture sets
    surface = None

MODELS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "maps", "models"))

ALPHA_CUTOFF = 0.5  # written into the leaf materials; the game should use the same
# The palettes below are a leaf's measured reflectance, and a crown painted
# with it comes out near black in the game: a real crown is brighter than its
# leaves' reflectance says, because light comes through the leaves as well as
# off them, and the game's leaf cards let none through. So the painted colour
# is raised to what a sunlit crown looks like next to sunlit grass.
LEAF_GAIN = 2.1
# How strongly the game's wind moves a plant (the `wind` custom property; see
# src/engine/materials.ts). Bark carries it too, or leaves would drift off their twigs.
WIND = 2.0
ATLAS_SIZE = 1024  # 2 x 2 sprays of 512 px
TREE_BUDGET = 2500
BUSH_BUDGET = 400
FAR_BUDGET = 150  # for the plain stand-ins; an impostor is 4 or 6 triangles
# Far away a tree or bush is an impostor: crossed cards showing its picture.
# The game lights a card evenly and cannot shade one part of a crown with
# another, so the picture is painted darker than the leaves: its opaque pixels
# average IMPOSTOR_GAIN x the average colour of the plant's leaf atlas.
IMPOSTOR_GAIN = 0.6
IMPOSTORS = True  # False: every stand-in is a plain lump, as it is when no picture can be rendered
CARDS_SIZE = 2048  # the shared atlas of plant pictures, `PlantCards_color`
CARD_PIXELS = (256, 96)  # the longer side of a picture: trees, bushes
CARD_MARGIN = 4  # around every picture, so neighbours stay out of each other's small copies
CARD_SAMPLES = 32
CARD_COVER = 0.3  # how much of a pixel the plant must cover to survive the cut-off: lower gives a fuller outline
CARD_SHADE = 0.45  # how dark the picture gets where the plant hides the whole sky (1 = no shading at all)
CARD_DEPTH = 1.0  # how fast it gets there: how much sky a leaf card hides, against what its size says
CARD_NORMAL = (0.5, 0.85)  # a card's normal at its outer edge: this much out from the trunk, this much up
CARD_LEAN = 0.35  # the most a card leans with its plant, metres sideways per metre up
WHITE_TILE = 64  # px, in the atlas's corner

UP = np.array([0.0, 0.0, 1.0])


class Plant(dict):
    """What the builders return; fields read as attributes or keys."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


# --- Small maths ---------------------------------------------------------------


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    length = np.linalg.norm(v)
    return v / length if length > 1e-12 else np.array([0.0, 0.0, 1.0])


def _perpendicular(v):
    """Some unit vector at right angles to v."""
    other = np.array([1.0, 0.0, 0.0]) if abs(v[0]) < 0.8 else np.array([0.0, 1.0, 0.0])
    return _unit(np.cross(v, other))


def _random_unit(rng):
    z = rng.uniform(-1.0, 1.0)
    angle = rng.uniform(0.0, math.tau)
    r = math.sqrt(1.0 - z * z)
    return np.array([r * math.cos(angle), r * math.sin(angle), z])


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _srgb(linear):
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(np.maximum(linear, 1e-9), 1 / 2.4) - 0.055)


# --- Painting the foliage textures -----------------------------------------------
# Everything is painted in linear colour, y up (row 0 is the bottom of the
# image, as Blender stores it), so texture v and canvas y agree.


class _Canvas:
    def __init__(self, size):
        self.size = size
        self.rgb = np.zeros((size, size, 3), dtype=np.float32)
        self.alpha = np.zeros((size, size), dtype=np.float32)
        self.normal = np.zeros((size, size, 3), dtype=np.float32)
        self.normal[..., 2] = 1.0

    def _window(self, x0, y0, x1, y1):
        x0, y0 = max(int(math.floor(x0)), 0), max(int(math.floor(y0)), 0)
        x1, y1 = min(int(math.ceil(x1)) + 1, self.size), min(int(math.ceil(y1)) + 1, self.size)
        if x1 <= x0 or y1 <= y0:
            return None
        yy, xx = np.mgrid[y0:y1, x0:x1]
        return (slice(y0, y1), slice(x0, x1)), xx + 0.5, yy + 0.5

    def stroke(self, ax, ay, bx, by, wa, wb, color):
        """A tapering line: twigs, leaf stalks."""
        pad = max(wa, wb) + 2
        window = self._window(min(ax, bx) - pad, min(ay, by) - pad, max(ax, bx) + pad, max(ay, by) + pad)
        if window is None:
            return
        where, xx, yy = window
        px, py = xx - ax, yy - ay
        dx, dy = bx - ax, by - ay
        t = np.clip((px * dx + py * dy) / (dx * dx + dy * dy + 1e-9), 0.0, 1.0)
        distance = np.hypot(px - t * dx, py - t * dy)
        cover = np.clip((wa + (wb - wa) * t) / 2 - distance + 0.5, 0.0, 1.0)
        self.rgb[where] = self.rgb[where] * (1 - cover[..., None]) + np.asarray(color, dtype=np.float32) * cover[..., None]
        self.alpha[where] = np.maximum(self.alpha[where], cover)

    def leaf(self, x, y, angle, length, shape, color, tilt, fold, phases, shadow=0.3):
        """One leaf with its base at (x, y), pointing along `angle`."""
        ca, sa = math.cos(angle), math.sin(angle)
        reach = length * (0.5 + shape["width"]) + 5
        cx, cy = x + ca * length / 2, y + sa * length / 2
        window = self._window(cx - reach, cy - reach, cx + reach, cy + reach)
        if window is None:
            return
        where, xx, yy = window
        px, py = xx - x, yy - y
        along = (px * ca + py * sa) / length
        across = (-px * sa + py * ca) / length
        side = np.where(across >= 0, 1.0, -1.0)
        half = _leaf_profile(shape, np.clip(along, 0.0, 1.0), side, phases)
        inside = (half - np.abs(across)) * length  # pixels in from the edge
        within = (along > 0.0) & (along < 1.0)
        cover = np.clip(inside + 0.5, 0.0, 1.0) * within
        if not cover.any():
            return
        if shadow > 0:  # a soft dark rim on whatever lies underneath
            rim = np.clip((inside + 5.0) / 5.0, 0.0, 1.0) * ((along > -0.05) & (along < 1.05))
            self.rgb[where] *= (1.0 - shadow * rim)[..., None]

        rel = np.clip(np.abs(across) / np.maximum(half, 1e-4), 0.0, 1.0)  # 0 on the midrib, 1 at the edge
        shade = (0.70 + 0.30 * np.clip(along, 0, 1)) * (1.0 + 0.055 * side * fold / (abs(fold) + 1e-6)) * (1.0 - 0.15 * rel**3)
        veins = np.cos(math.tau * (along * shape["veins"] - rel * 0.85))
        shade = shade * (1.0 + 0.10 * (veins > 0.9))
        tone = np.asarray(color, dtype=np.float32)[None, None, :] * shade[..., None]
        rib_width = 0.6 + length * 0.012 * (1.0 - 0.6 * np.clip(along, 0, 1))
        rib = np.clip(rib_width - np.abs(across) * length + 0.5, 0.0, 1.0)[..., None] * 0.75
        rib_tone = np.asarray(color, dtype=np.float32) * 1.45 + np.array([0.012, 0.016, 0.0], dtype=np.float32)
        tone = tone * (1 - rib) + rib_tone * rib

        c = cover[..., None]
        self.rgb[where] = self.rgb[where] * (1 - c) + tone * c
        self.alpha[where] = np.maximum(self.alpha[where], cover)

        # The leaf's own slope: tilted as a whole, folded along the midrib, curled along its length.
        n_across = -fold * side * (1.0 - 0.5 * rel) + tilt[1]
        n_along = tilt[0] + 0.35 * (np.clip(along, 0, 1) - 0.5)
        nx = n_along * ca - n_across * sa
        ny = n_along * sa + n_across * ca
        scale = 1.0 / np.sqrt(nx * nx + ny * ny + 1.0)
        normal = np.stack([nx * scale, ny * scale, scale], axis=-1)
        self.normal[where] = self.normal[where] * (1 - c) + normal * c

    def needles(self, starts, ends, colors, width=2):
        """Many short straight needles at once; meant for a canvas later scaled down."""
        starts, ends = np.asarray(starts, dtype=np.float32), np.asarray(ends, dtype=np.float32)
        colors = np.asarray(colors, dtype=np.float32)
        lengths = np.hypot(*(ends - starts).T)
        steps = int(max(2, math.ceil(float(lengths.max()) * 1.3)))
        direction = (ends - starts) / np.maximum(lengths, 1e-6)[:, None]
        slope = np.stack([-direction[:, 1], direction[:, 0]], axis=1) * 0.55  # needles are round: lit across their width
        for step in range(steps):
            t = step / (steps - 1)
            point = starts + (ends - starts) * t
            tone = colors * (0.72 + 0.4 * t)  # paler toward the tip
            for ox in range(width):
                for oy in range(width):
                    xi = np.clip((point[:, 0] + ox).astype(np.int32), 0, self.size - 1)
                    yi = np.clip((point[:, 1] + oy).astype(np.int32), 0, self.size - 1)
                    self.rgb[yi, xi] = tone
                    self.alpha[yi, xi] = 1.0
                    lean = (ox + oy) / max(width - 1, 1) - 1.0
                    self.normal[yi, xi, 0] = slope[:, 0] * lean
                    self.normal[yi, xi, 1] = slope[:, 1] * lean
                    self.normal[yi, xi, 2] = 1.0

    def reduced(self, factor):
        """The canvas averaged down by `factor` (anti-aliasing for the needle sprays)."""
        small = _Canvas(self.size // factor)
        shape = (small.size, factor, small.size, factor)
        alpha = self.alpha.reshape(shape).mean(axis=(1, 3))
        weighted = (self.rgb * self.alpha[..., None]).reshape(*shape, 3).mean(axis=(1, 3))
        small.rgb = weighted / np.maximum(alpha, 1e-5)[..., None]
        small.alpha = alpha
        small.normal = self.normal.reshape(*shape, 3).mean(axis=(1, 3))
        return small

    def bleed(self, passes=24):
        """
        Spreads colour outward into the transparent pixels. Without it the
        cut-out edge picks up black (or whatever lies outside) when the texture
        is filtered and mip-mapped, and every leaf gets a dark halo.
        """
        filled = self.alpha > 0.02
        if not filled.any():
            return
        mean = self.rgb[filled].mean(axis=0)
        rgb = np.where(filled[..., None], self.rgb, 0.0).astype(np.float32)
        weight = filled.astype(np.float32)
        for _ in range(passes):
            total = np.zeros_like(rgb)
            count = np.zeros_like(weight)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    total += np.roll(rgb * weight[..., None], (dy, dx), axis=(0, 1))
                    count += np.roll(weight, (dy, dx), axis=(0, 1))
            grow = (weight == 0) & (count > 0)
            rgb[grow] = total[grow] / count[grow][:, None]
            weight[grow] = 1.0
        rgb[weight == 0] = mean
        self.rgb = rgb


def _leaf_profile(shape, along, side, phases):
    """Half the leaf's width (as a fraction of its length) at `along` 0..1 from base to tip."""
    kind = shape["kind"]
    if kind == "lobed":  # oak: widest beyond the middle, rounded lobes that differ side to side
        base = np.sin(np.pi * along**1.25) ** 0.75
        # Round lobes with narrow notches between them, not a zigzag.
        notch = np.abs(np.sin(np.pi * shape["lobes"] * along + np.where(side > 0, phases[0], phases[1]))) ** 3
        return shape["width"] * base * (1.0 - 0.46 * notch)
    if kind == "lance":  # ash leaflets, willow
        return shape["width"] * np.sin(np.pi * along**0.85) ** 0.9
    # ovate with a toothed edge: hawthorn, apple, elm, most hedge shrubs
    base = np.sin(np.pi * along**0.68) ** 0.85
    teeth = 1.0 + 0.07 * (np.mod(along * shape.get("teeth", 11.0) + phases[0], 1.0) - 0.5)
    return shape["width"] * base * teeth


LEAVES = {
    # `leaf` is the leaf's length as a fraction of the tile; with `card` (the
    # size in metres a tree draws one tile at) that fixes how big a leaf is.
    "LeavesOak": dict(
        shape=dict(kind="lobed", width=0.29, lobes=3.6, veins=5.0),
        color=(0.056, 0.102, 0.023), leaf=0.080, spacing=0.034, side_spacing=0.085, compound=0, seed=11, card=1.4,
    ),
    "LeavesAsh": dict(
        shape=dict(kind="lance", width=0.15, veins=8.0),
        color=(0.064, 0.118, 0.029), leaf=0.064, spacing=0.085, side_spacing=0.13, compound=4, seed=23, card=1.3,
    ),
    "LeavesThorn": dict(
        shape=dict(kind="ovate", width=0.29, veins=6.0, teeth=12.0),
        color=(0.046, 0.092, 0.022), leaf=0.058, spacing=0.026, side_spacing=0.07, compound=0, seed=37, card=1.1,
    ),
}
TWIG = (0.060, 0.044, 0.030)


def _leaf_tone(rng, base):
    """A leaf's own colour: most a little different, a few yellowing, the odd brown one."""
    tone = np.asarray(base) * math.exp(rng.gauss(0.0, 0.22))
    warm = rng.uniform(-1.0, 1.0)
    tone = tone * np.array([1.0 + 0.16 * warm, 1.0 + 0.03 * warm, 1.0 - 0.25 * warm])
    chance = rng.random()
    if chance < 0.07:
        tone = tone + (np.array([0.17, 0.14, 0.018]) - tone) * rng.uniform(0.25, 0.8)
    elif chance < 0.10:
        tone = tone + (np.array([0.07, 0.04, 0.014]) - tone) * rng.uniform(0.4, 0.9)
    elif chance < 0.26:  # catching the sky: paler and greyer
        tone = tone * 1.18 + np.array([0.006, 0.009, 0.007])
    return tone


def _paint_leaf_spray(canvas, ox, oy, tile, rng, spec):
    """One leafy twig, growing up from the middle of the tile's bottom edge."""
    shoots = []  # (points [(x, y, angle)], width, level, length), in tile units

    def grow(x, y, angle, length, width, level):
        count = max(3, int(length / 0.05))
        bend = rng.uniform(-0.4, 0.4)
        points = [(x, y, angle)]
        for i in range(count):
            heading = angle + bend * (i + 1) / count + rng.gauss(0.0, 0.06)
            x += math.cos(heading) * length / count
            y += math.sin(heading) * length / count
            points.append((x, y, heading))
        shoots.append((points, width, level, length))
        if level < 2:
            sides = max(1, int(round(length / spec["side_spacing"])))
            side = rng.choice((-1, 1))
            for j in range(sides):
                t = 0.12 + 0.8 * (j + rng.uniform(0.2, 0.8)) / sides
                px, py, heading = _along(points, t)
                reach = length * rng.uniform(0.5, 0.75) * (1.0 - 0.45 * t)
                if reach > 0.07:
                    grow(px, py, heading + side * rng.uniform(0.5, 1.0), reach, width * 0.62, level + 1)
                side = -side

    lean = rng.uniform(-0.1, 0.1)
    grow(0.5, -0.01, math.pi / 2 + lean, rng.uniform(0.76, 0.82), 0.017, 0)
    for hand in (-1, 1):  # two more from the foot, so the spray is a fan and not a feather
        grow(0.5, 0.03, math.pi / 2 + lean + hand * rng.uniform(0.55, 0.85), rng.uniform(0.40, 0.52), 0.011, 1)

    # The spray keeps inside a lumpy outline of its own, well clear of the
    # tile's edges, and has a hole or two: a card must never show as a square.
    waves = [(k, rng.uniform(0.05, 0.11), rng.uniform(0.0, math.tau)) for k in (2, 3, 4, 5)]
    holes = [(rng.uniform(0.2, 0.8), rng.uniform(0.3, 0.85), rng.uniform(0.04, 0.075)) for _ in range(3)]

    def within(x, y):
        """Inside the spray's outline (where twigs may run)."""
        dx, dy = x - 0.5, y - 0.53
        angle = math.atan2(dy, dx)
        limit = 0.45 * (1.0 + sum(amount * math.cos(k * angle + phase) for k, amount, phase in waves))
        if math.hypot(dx, dy * 0.98) > min(limit, 0.485) or not (0.015 < x < 0.985 and 0.015 < y < 0.985):
            return abs(dx) < 0.05 and -0.02 < y < 0.2  # the foot of the stem
        return True

    def inside(x, y):
        """Inside the outline and not in one of its holes (where leaves may be)."""
        return within(x, y) and all(math.hypot(x - hx, y - hy) > hr for hx, hy, hr in holes)

    leaves = []  # (x, y, angle, length)
    stalks = []  # leaf stalks and the spines of compound leaves
    leaf, width = spec["leaf"], spec["shape"]["width"]

    def add_leaf(x, y, angle, length):
        tip = (x + math.cos(angle) * length, y + math.sin(angle) * length)
        mid = (x + math.cos(angle) * length * 0.5, y + math.sin(angle) * length * 0.5)
        edge = (-math.sin(angle) * length * width, math.cos(angle) * length * width)
        if inside(*tip) and inside(x, y) and inside(mid[0] + edge[0], mid[1] + edge[1]) and inside(mid[0] - edge[0], mid[1] - edge[1]):
            leaves.append((x, y, angle, length))
            return True
        return False

    def add_foliage(x, y, angle):
        if not spec["compound"]:
            stalk = leaf * 0.18
            sx, sy = x + math.cos(angle) * stalk, y + math.sin(angle) * stalk
            if add_leaf(sx, sy, angle + rng.gauss(0.0, 0.12), leaf * rng.uniform(0.72, 1.15)):
                stalks.append((x, y, sx, sy, 0.004))
            return
        # A compound leaf: a spine with pairs of leaflets and one at the end.
        pairs = spec["compound"] + rng.choice((0, 1))
        spine = leaf * 0.62 * (pairs + 1.2)
        ex, ey = x + math.cos(angle) * spine, y + math.sin(angle) * spine
        if not (inside(ex, ey) and inside(x, y)):
            return
        before = len(leaves)
        for pair in range(pairs):
            t = (pair + 1.3) / (pairs + 1.2)
            px, py = x + (ex - x) * t, y + (ey - y) * t
            for hand in (-1, 1):
                add_leaf(px, py, angle + hand * rng.uniform(0.7, 1.0), leaf * rng.uniform(0.8, 1.1) * (1.0 - 0.35 * abs(t - 0.55)))
        add_leaf(ex, ey, angle + rng.gauss(0.0, 0.1), leaf * rng.uniform(0.9, 1.15))
        if len(leaves) - before < 0.7 * (2 * pairs + 1):
            del leaves[before:]  # most of it would be cut off at the outline: no bare spines
        else:
            stalks.append((x, y, ex, ey, 0.0045))

    def living(points):
        """How much of a shoot (a fraction of its length) lies inside the outline before it first leaves it."""
        for i, point in enumerate(points):
            if not within(point[0], point[1]):
                return max(i - 1, 0) / (len(points) - 1)
        return 1.0

    for points, _, level, length in shoots:
        alive = living(points)
        s = 0.11 if level == 0 else 0.015
        side = rng.choice((-1, 1))
        while s < length * alive:
            x, y, heading = _along(points, s / length)
            if spec["compound"]:  # ash leaves come in opposite pairs
                for hand in (-1, 1):
                    add_foliage(x, y, heading + hand * rng.uniform(0.6, 1.0))
            else:
                add_foliage(x, y, heading + side * rng.uniform(0.45, 1.2))
                side = -side
            s += spec["spacing"] * rng.uniform(0.7, 1.3)
        x, y, heading = _along(points, alive)
        for _ in range((1 if spec["compound"] else 3) if alive > 0 else 0):
            add_foliage(x, y, heading + rng.uniform(-0.55, 0.55))

    def px(value):
        return value * tile

    for points, width, _, _ in shoots:
        alive = living(points)
        for i in range(len(points) - 1):
            t0, t1 = i / (len(points) - 1), (i + 1) / (len(points) - 1)
            if t1 > alive + 1e-6:
                break  # no bare twigs poking out past the leaves
            a, b = points[i], points[i + 1]
            canvas.stroke(ox + px(a[0]), oy + px(a[1]), ox + px(b[0]), oy + px(b[1]), px(width) * (1.0 - 0.6 * t0), px(width) * (1.0 - 0.6 * t1), TWIG)
    stalk_tone = np.asarray(spec["color"]) * 1.1 + np.asarray(TWIG) * 0.5
    for ax, ay, bx, by, width in stalks:
        canvas.stroke(ox + px(ax), oy + px(ay), ox + px(bx), oy + px(by), px(width), px(width) * 0.7, stalk_tone)

    # Leaves from the back of the spray to the front: the ones behind are in
    # the shade of the ones in front, and those near the stem darker still.
    order = [(rng.random(), item) for item in leaves]
    order.sort(key=lambda entry: entry[0])
    for depth, (x, y, angle, length) in order:
        reach = min(1.0, math.hypot(x - 0.5, y) / 0.6)
        light = (0.46 + 0.54 * depth**0.8) * (0.74 + 0.26 * reach)
        tone = _leaf_tone(rng, spec["color"]) * light
        canvas.leaf(
            ox + px(x), oy + px(y), angle, px(length), spec["shape"], tone,
            tilt=(rng.gauss(0.0, 0.28), rng.gauss(0.0, 0.28)), fold=rng.uniform(0.15, 0.5) * rng.choice((-1, 1)),
            phases=(rng.uniform(0, math.tau), rng.uniform(0, math.tau)),
        )


def _along(points, t):
    """The point (and heading) a fraction t of the way along a list of (x, y, angle)."""
    k = min(max(t, 0.0), 1.0) * (len(points) - 1)
    i = min(int(k), len(points) - 2)
    f = k - i
    a, b = points[i], points[i + 1]
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, b[2]


NEEDLES = {
    "NeedlesSpruce": dict(color=(0.021, 0.046, 0.019), tip=(0.055, 0.10, 0.032), seed=53, card=1.2),
}


def _paint_needle_spray(canvas, ox, oy, tile, rng, spec, narrow=False):
    """
    A conifer frond seen flat: a shoot with side shoots in a herringbone, each
    a bottle-brush of short needles. Two of them are laid one over the other,
    the one behind in shade, so the frond has depth and gaps instead of being
    one flat shape. Painted on a canvas twice the final size.
    """

    def layout():
        shoots = []  # (points [(x, y)], level)

        def grow(x, y, angle, length, level):
            count = max(2, int(length / 0.04))
            bend = rng.uniform(-0.3, 0.3) * (1.0 if level else 0.4)
            points = [(x, y)]
            headings = []
            for i in range(count):
                heading = angle + bend * (i + 1) / count + rng.gauss(0.0, 0.04)
                x += math.cos(heading) * length / count
                y += math.sin(heading) * length / count
                points.append((x, y))
                headings.append(heading)
            shoots.append((points, level))
            if level < 2:
                spacing = 0.082 if level == 0 else 0.075
                sides = int(length / spacing)
                for j in range(sides):
                    t = (j + 0.7 + rng.uniform(-0.25, 0.25)) / (sides + 0.5)
                    k = t * count
                    i = min(int(k), count - 1)
                    f = k - i
                    px = points[i][0] + (points[i + 1][0] - points[i][0]) * f
                    py = points[i][1] + (points[i + 1][1] - points[i][1]) * f
                    if level == 0:  # longest a third of the way out, short at the tip
                        reach = (0.30 if narrow else 0.46) * (0.5 + 0.5 * math.sin(math.pi * min(1.0, t * 1.5 + 0.12) ** 0.9)) * (1.0 - 0.62 * t**2.0)
                    else:
                        reach = length * 0.40 * (1.0 - 0.5 * t)
                    reach *= rng.uniform(0.7, 1.15)
                    if reach > 0.04:
                        hands = (-1, 1) if level == 0 else ((rng.choice((-1, 1)),) if rng.random() < 0.5 else (-1, 1))
                        for hand in hands:
                            turn = rng.uniform(0.6, 0.95) * (0.75 if narrow else 1.0) * (1.0 if level == 0 else 0.85)
                            grow(px, py, headings[i] + hand * turn, reach * rng.uniform(0.85, 1.1), level + 1)

        grow(0.5 + rng.uniform(-0.03, 0.03), 0.0, math.pi / 2 + rng.uniform(-0.06, 0.06), rng.uniform(0.84, 0.93), 0)
        return shoots

    base, pale = np.asarray(spec["color"]), np.asarray(spec["tip"])
    needle = 0.024 * tile
    offset = np.array([ox, oy], dtype=np.float32)
    for light in (0.42, 1.0):  # the frond behind, then the one in front
        starts, ends, colors = [], [], []
        wood = []
        for points, level in layout():
            segments = len(points) - 1
            for i in range(segments):
                a, b = np.array(points[i]) * tile, np.array(points[i + 1]) * tile
                heading = math.atan2(b[1] - a[1], b[0] - a[0])
                wood.append((a, b))
                steps = max(1, int(np.hypot(*(b - a)) / 1.1))
                for step in range(steps):
                    t = (i + step / steps) / segments  # 0 at the shoot's base, 1 at its tip
                    p = a + (b - a) * (step / steps)
                    fresh = _smoothstep(0.45, 1.0, t) * (0.3 + 0.7 * (level > 0)) * rng.uniform(0.4, 1.0)
                    tone = (base + (pale - base) * fresh) * math.exp(rng.gauss(0.0, 0.25)) * (0.55 + 0.45 * t) * light
                    for hand in (-1, 1):
                        lean = heading + hand * rng.uniform(0.5, 1.1)
                        reach = needle * rng.uniform(0.65, 1.1) * (1.0 - 0.3 * (t > 0.93))
                        starts.append(p)
                        ends.append(p + reach * np.array([math.cos(lean), math.sin(lean)]))
                        colors.append(tone)
        # Dark needles go down first and the bright ones lie on top.
        order = np.argsort([c[1] + rng.uniform(0.0, 0.015) for c in colors])
        canvas.needles(np.array(starts)[order] + offset, np.array(ends)[order] + offset, np.array(colors)[order])
        for a, b in wood:  # the shoot itself shows through as a thin brown line
            canvas.stroke(ox + a[0], oy + a[1], ox + b[0], oy + b[1], 2.4, 2.4, np.asarray(TWIG) * 0.8 * light)


def _atlas(name):
    """The (colour, normal) images for one kind of foliage, painted on first use and packed."""
    color_name, normal_name = f"{name}_color", f"{name}_normal"
    if color_name in bpy.data.images and normal_name in bpy.data.images:
        return bpy.data.images[color_name], bpy.data.images[normal_name]

    tile = ATLAS_SIZE // 2
    if name in NEEDLES:
        spec = NEEDLES[name]
        canvas = _Canvas(ATLAS_SIZE * 2)
        for index in range(4):
            rng = random.Random(spec["seed"] * 1000 + index)
            _paint_needle_spray(canvas, (index % 2) * tile * 2, (index // 2) * tile * 2, tile * 2, rng, spec, narrow=index >= 2)
        canvas = canvas.reduced(2)
    else:
        spec = LEAVES[name]
        canvas = _Canvas(ATLAS_SIZE)
        for index in range(4):
            rng = random.Random(spec["seed"] * 1000 + index)
            _paint_leaf_spray(canvas, (index % 2) * tile, (index // 2) * tile, tile, rng, spec)
    canvas.bleed()
    canvas.rgb = np.clip(canvas.rgb * LEAF_GAIN, 0.0, 1.0)

    size = canvas.size
    covered = canvas.alpha > 0.5
    average = canvas.rgb[covered].mean(axis=0) if covered.any() else np.asarray(spec["color"])

    color = bpy.data.images.new(color_name, size, size, alpha=True)
    color.alpha_mode = "STRAIGHT"
    pixels = np.concatenate([_srgb(canvas.rgb), canvas.alpha[..., None]], axis=-1)  # 8-bit colour is stored display-encoded
    color.pixels.foreach_set(pixels.astype(np.float32).ravel())
    color.pack()
    color["average"] = [float(c) for c in average]
    color["coverage"] = float(covered.mean())

    normal = bpy.data.images.new(normal_name, size, size, alpha=False)
    normal.colorspace_settings.name = "Non-Color"
    lengths = np.linalg.norm(canvas.normal, axis=-1, keepdims=True)
    encoded = canvas.normal / np.maximum(lengths, 1e-6) * 0.5 + 0.5
    normal.pixels.foreach_set(np.concatenate([encoded, np.ones((size, size, 1))], axis=-1).astype(np.float32).ravel())
    normal.pack()
    return color, normal


# --- Materials -------------------------------------------------------------------


def _new_material(name):
    result = bpy.data.materials.new(name)
    if result.node_tree is None:
        result.use_nodes = True  # Blender < 5: node trees are opt-in
    nodes, links = result.node_tree.nodes, result.node_tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return result, nodes, links, shader


def flat_material(name, color, roughness=0.9):
    """A plain opaque material, shared by name."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    result, _, _, shader = _new_material(name)
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    return result


def bark_material():
    """The level's `Bark` if it has one, else the bark texture set, else plain brown."""
    if "Bark" in bpy.data.materials:
        result = bpy.data.materials["Bark"]
    else:
        result = surface("Bark", "bark_brown_02") if surface is not None else None
        if result is None:
            result = flat_material("Bark", (0.10, 0.058, 0.03), roughness=0.95)
        else:
            result["detile"] = 1.0  # as level scripts set on their own bark: lets the game shuffle its repeats
    if "wind" not in result:
        result["wind"] = WIND
    return result


def foliage_material(name):
    """
    The cut-out leaf (or needle) material of that name: texture x the `Col`
    colour attribute, alpha clipped at ALPHA_CUTOFF (which Blender's glTF
    exporter writes as alphaMode MASK), a normal map, drawn from both sides.
    """
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    color_image, normal_image = _atlas(name)
    result, nodes, links, shader = _new_material(name)
    x, y = shader.location.x, shader.location.y

    color = nodes.new("ShaderNodeTexImage")
    color.image = color_image
    color.location = (x - 720, y + 160)
    painted = nodes.new("ShaderNodeVertexColor")
    painted.layer_name = "Col"
    painted.location = (x - 620, y + 380)
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    mix.location = (x - 300, y + 200)
    links.new(color.outputs["Color"], mix.inputs["A"])
    links.new(painted.outputs["Color"], mix.inputs["B"])
    links.new(mix.outputs["Result"], shader.inputs["Base Color"])

    # alpha = 1 - (alpha < cutoff): the pattern the exporter reads as a cut-out.
    below = nodes.new("ShaderNodeMath")
    below.operation = "LESS_THAN"
    below.inputs[1].default_value = ALPHA_CUTOFF
    below.location = (x - 420, y - 60)
    invert = nodes.new("ShaderNodeMath")
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    invert.location = (x - 240, y - 60)
    links.new(color.outputs["Alpha"], below.inputs[0])
    links.new(below.outputs["Value"], invert.inputs[1])
    links.new(invert.outputs["Value"], shader.inputs["Alpha"])

    bumps = nodes.new("ShaderNodeTexImage")
    bumps.image = normal_image
    bumps.location = (x - 720, y - 320)
    bump = nodes.new("ShaderNodeNormalMap")
    bump.inputs["Strength"].default_value = 1.0
    bump.location = (x - 300, y - 320)
    links.new(bumps.outputs["Color"], bump.inputs["Color"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])

    shader.inputs["Roughness"].default_value = 0.62
    result.use_backface_culling = False  # -> doubleSided
    for setting, value in (("use_backface_culling_shadow", False), ("use_transparent_shadow", True), ("surface_render_method", "DITHERED")):
        if hasattr(result, setting):
            setattr(result, setting, value)
    spec = LEAVES.get(name) or NEEDLES[name]
    result["alpha_cutoff"] = ALPHA_CUTOFF
    result["foliage"] = True
    result["wind"] = WIND
    result["card_metres"] = spec["card"]
    return result


def far_material(name, foliage):
    """The plain colour a kind of foliage averages out to, a little darkened for the shade inside a crown."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    average = np.asarray(_atlas(foliage)[0]["average"])
    # Well below the leaves' own colour: a real crown is half in its own shade,
    # and a stand-in at the leaf colour reads as a pale balloon next to it.
    return flat_material(name, tuple(float(c) for c in average * 0.42), roughness=0.9)


# --- Building meshes -------------------------------------------------------------


class _Geometry:
    """Collects faces with everything stored per corner: texture UV, wind UV, normal, colour."""

    def __init__(self):
        self.vertices = []
        self.faces = []
        self.slots = []
        self.uv = []
        self.wind = []
        self.normals = []
        self.colors = []
        self.triangles = 0

    def face(self, points, uvs, winds, normals, colors, slot):
        # Wound to agree with the normals given, so a renderer that flips normals on back faces lights the front correctly.
        corners = [np.asarray(p, dtype=np.float64) for p in points]
        facing = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        if float(np.dot(facing, np.sum(np.asarray(normals, dtype=np.float64), axis=0))) < 0:
            points, uvs, winds, normals, colors = (list(reversed(each)) for each in (points, uvs, winds, normals, colors))
        start = len(self.vertices)
        self.vertices.extend(tuple(p) for p in points)
        self.faces.append(tuple(range(start, start + len(points))))
        self._corners(uvs, winds, normals, colors, slot, len(points))

    def _corners(self, uvs, winds, normals, colors, slot, count):
        self.slots.append(slot)
        self.uv.extend(uvs)
        self.wind.extend(winds)
        self.normals.extend(normals)
        self.colors.extend(colors)
        self.triangles += count - 2

    def tube(self, points, radii, sides, winds, phase, slot, metres=1.0, v_start=0.0, tip=True):
        """
        A limb: rings of `sides` vertices around a line of points, closed to a
        point at the end. Bark runs along it at `metres` per repeat and wraps
        around it a whole number of times.
        """
        points = np.asarray(points, dtype=np.float64)
        count = len(points)
        tangents = np.empty_like(points)
        tangents[0] = points[1] - points[0]
        tangents[-1] = points[-1] - points[-2]
        tangents[1:-1] = points[2:] - points[:-2]
        tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-9)
        normal = _perpendicular(tangents[0])
        wraps = max(1, int(round(math.tau * radii[0] / metres)))
        distance = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
        white = (1.0, 1.0, 1.0)

        rings = []  # per ring: (first vertex index, [normal per side])
        last = count - 1 if tip else count
        for i in range(last):
            normal = _unit(normal - tangents[i] * np.dot(normal, tangents[i]))  # carried along, never twisting
            binormal = np.cross(tangents[i], normal)
            start = len(self.vertices)
            around = []
            for j in range(sides):
                angle = math.tau * j / sides
                out = math.cos(angle) * normal + math.sin(angle) * binormal
                self.vertices.append(tuple(points[i] + out * radii[i]))
                around.append(tuple(out))
            rings.append((start, around))
        for i in range(len(rings) - 1):
            (a, normals_a), (b, normals_b) = rings[i], rings[i + 1]
            va, vb = v_start + distance[i] / metres, v_start + distance[i + 1] / metres
            for j in range(sides):
                k = (j + 1) % sides
                u0, u1 = wraps * j / sides, wraps * (j + 1) / sides
                self.faces.append((a + j, a + k, b + k, b + j))
                self._corners(
                    [(u0, va), (u1, va), (u1, vb), (u0, vb)],
                    [(winds[i], phase), (winds[i], phase), (winds[i + 1], phase), (winds[i + 1], phase)],
                    [normals_a[j], normals_a[k], normals_b[k], normals_b[j]],
                    [white] * 4, slot, 4,
                )
        if tip:
            a, normals_a = rings[-1]
            end = len(self.vertices)
            self.vertices.append(tuple(points[-1]))
            va, vb = v_start + distance[-2] / metres, v_start + distance[-1] / metres
            for j in range(sides):
                k = (j + 1) % sides
                u0, u1 = wraps * j / sides, wraps * (j + 1) / sides
                self.faces.append((a + j, a + k, end))
                self._corners(
                    [(u0, va), (u1, va), ((u0 + u1) / 2, vb)],
                    [(winds[-2], phase), (winds[-2], phase), (winds[-1], phase)],
                    [normals_a[j], normals_a[k], tuple(_unit(np.asarray(normals_a[j]) + np.asarray(normals_a[k])))],
                    [white] * 3, slot, 3,
                )

    def to_mesh(self, name, materials, colors=True):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.uv_layers.new(name="UVMap", do_init=False).data.foreach_set("uv", np.asarray(self.uv, dtype=np.float32).ravel())
        mesh.uv_layers.new(name="wind", do_init=False).data.foreach_set("uv", np.asarray(self.wind, dtype=np.float32).ravel())
        mesh.uv_layers.active = mesh.uv_layers["UVMap"]
        mesh.uv_layers["UVMap"].active_render = True
        if colors:
            tones = np.clip(np.asarray(self.colors, dtype=np.float32), 0.0, 1.0)
            attribute = mesh.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
            attribute.data.foreach_set("color", np.concatenate([tones, np.ones((len(tones), 1), dtype=np.float32)], axis=1).ravel())
        else:
            mesh.use_fake_user = True  # a stand-in is on no object, and must still be saved with the file
        mesh.polygons.foreach_set("material_index", np.asarray(self.slots, dtype=np.int32))
        mesh.polygons.foreach_set("use_smooth", np.ones(len(self.faces), dtype=bool))
        for material in materials:
            mesh.materials.append(material)
        mesh.update()
        normals = np.asarray(self.normals, dtype=np.float64)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        mesh.normals_split_custom_set(normals.tolist())
        mesh.update()
        return mesh


_BLOBS = {}


def _blob(detail=1):
    """A ball of 20 triangles (detail 1) or 80 (detail 2): unit vertices and faces."""
    if detail not in _BLOBS:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=detail, radius=1.0)
        bm.verts.ensure_lookup_table()
        _BLOBS[detail] = (np.array([v.co[:] for v in bm.verts]), [tuple(v.index for v in f.verts) for f in bm.faces])
        bm.free()
    return _BLOBS[detail]


def _add_shell(geometry, foliage, centre, radii, pad, slot, wind):
    """
    An 80-triangle skin pulled over a crown: each of its points is set as far
    from `centre` as the foliage (an array of points) reaches in that
    direction, and drawn in where there is none, so the stand-in has the
    plant's own outline, hollows and all.
    """
    directions, faces = _blob(2)
    reach = (np.asarray(foliage) - centre) / radii  # the crown as a unit ball
    distance = np.linalg.norm(reach, axis=1)
    toward = reach / np.maximum(distance, 1e-6)[:, None]
    placed, normals = [], []
    for direction in directions:
        aligned = _smoothstep(0.78, 0.96, toward @ direction)  # foliage within about 35 degrees, the nearer in line the more it counts
        r = max(0.3, 0.94 * float((distance * aligned).max()))
        point = centre + direction * r * radii
        out = _unit(direction / radii)
        placed.append(point + out * pad)
        normals.append(_unit(out * 0.8 + UP * 0.3))
    start = len(geometry.vertices)
    geometry.vertices.extend(tuple(p) for p in placed)
    for face in faces:
        a, b, c = (placed[i] for i in face)
        order = face if float(np.dot(np.cross(b - a, c - a), (a + b + c) / 3 - centre)) >= 0 else face[::-1]
        geometry.faces.append(tuple(start + i for i in order))
        geometry._corners([(0.5, 0.5)] * 3, [wind] * 3, [tuple(normals[i]) for i in order], [(1.0, 1.0, 1.0)] * 3, slot, 3)


def _add_blob(geometry, centre, radii, slot, wind, crown_centre, turn=0.0):
    vertices, faces = _blob(1)
    c, s = math.cos(turn), math.sin(turn)
    spin = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    placed = (vertices @ spin.T) * np.asarray(radii) + np.asarray(centre)
    start = len(geometry.vertices)
    geometry.vertices.extend(tuple(p) for p in placed)
    normals = [_unit(_unit(p - np.asarray(centre)) + 0.6 * _unit(p - np.asarray(crown_centre))) for p in placed]
    for face in faces:
        geometry.faces.append(tuple(start + i for i in face))
        geometry._corners([(0.5, 0.5)] * 3, [wind] * 3, [tuple(normals[i]) for i in face], [(1.0, 1.0, 1.0)] * 3, slot, 3)


# --- Distant stand-ins: impostor cards -------------------------------------------------
# Far away a plant is two or three upright cards crossed on its trunk, showing
# a picture of that plant. All the pictures share one atlas and one material,
# so a chunk's far mesh stays one draw.


def _card_tiles(image):
    """The atlas's tiles so far, oldest first: [name, x, y, width, height] each, margins included."""
    return json.loads(image["tiles"])


def _tile_spots(sizes, size=None, shelves=None):
    """
    Where tiles of these (width, height) go in the atlas, in the order given;
    None for one that does not fit. Shelves run across it from the bottom up,
    one height for bushes and one for trees, and a tile lighter than its shelf
    shares a column with others. Worked out afresh from the list each time, so
    the list is all that has to be kept with the image.
    """
    size = size or CARDS_SIZE
    heights = shelves or sorted(pixels + 2 * CARD_MARGIN for pixels in CARD_PIXELS)
    rows, spots = [], []
    for width, height in sizes:
        spot = None
        for row in rows:
            if height > row["height"]:
                continue
            for column in row["columns"]:
                if width <= column["width"] and column["used"] + height <= row["height"]:
                    spot = (column["x"], row["y"] + column["used"])
                    column["used"] += height
                    break
            if spot is None and row["x"] + width <= size:
                row["columns"].append(dict(x=row["x"], width=width, used=height))
                spot = (row["x"], row["y"])
                row["x"] += width
            if spot is not None:
                break
        if spot is None:
            y = rows[-1]["y"] + rows[-1]["height"] if rows else 0
            tall = next((each for each in heights if each >= height), height)
            if y + tall <= size and width <= size:
                rows.append(dict(y=y, height=tall, x=width, columns=[dict(x=0, width=width, used=height)]))
                spot = (0, y)
        spots.append(spot)
    return spots


def _cards_image():
    """The shared atlas `PlantCards_color`, made on first use with only its white tile."""
    name = "PlantCards_color"
    image = bpy.data.images.get(name)
    if image is not None:
        if tuple(image.size) != (CARDS_SIZE, CARDS_SIZE) or "tiles" not in image:
            raise RuntimeError(f"an image named {name} exists already and is not this module's atlas")
        return image
    image = bpy.data.images.new(name, CARDS_SIZE, CARDS_SIZE, alpha=True)
    image.alpha_mode = "STRAIGHT"
    pixels = np.zeros((CARDS_SIZE, CARDS_SIZE, 4), dtype=np.float32)
    pixels[:WHITE_TILE, :WHITE_TILE] = 1.0  # for whatever shares the cards' material and has no picture
    image.pixels.foreach_set(pixels.ravel())
    image.pack()
    image["tiles"] = json.dumps([["white", 0, 0, WHITE_TILE, WHITE_TILE]])
    return image


def cards_material():
    """
    The one material of every impostor card, `PlantCards`: the atlas as base
    colour, alpha clipped at ALPHA_CUTOFF (glTF alphaMode MASK), drawn from
    both sides. `white_uv` is a spot in the atlas that is opaque white, for
    geometry that is to share the material without a picture.
    """
    image = _cards_image()
    if "PlantCards" in bpy.data.materials:
        return bpy.data.materials["PlantCards"]
    result, nodes, links, shader = _new_material("PlantCards")
    x, y = shader.location.x, shader.location.y
    color = nodes.new("ShaderNodeTexImage")
    color.image = image
    color.location = (x - 720, y + 160)
    links.new(color.outputs["Color"], shader.inputs["Base Color"])
    # alpha = 1 - (alpha < cutoff), as in foliage_material.
    below = nodes.new("ShaderNodeMath")
    below.operation = "LESS_THAN"
    below.inputs[1].default_value = ALPHA_CUTOFF
    below.location = (x - 420, y - 60)
    invert = nodes.new("ShaderNodeMath")
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    invert.location = (x - 240, y - 60)
    links.new(color.outputs["Alpha"], below.inputs[0])
    links.new(below.outputs["Value"], invert.inputs[1])
    links.new(invert.outputs["Value"], shader.inputs["Alpha"])
    shader.inputs["Roughness"].default_value = 0.9
    result.use_backface_culling = False  # -> doubleSided
    for setting, value in (("use_backface_culling_shadow", False), ("use_transparent_shadow", True), ("surface_render_method", "DITHERED")):
        if hasattr(result, setting):
            setattr(result, setting, value)
    result["impostor"] = True
    result["alpha_cutoff"] = ALPHA_CUTOFF
    result["white_uv"] = [WHITE_TILE / 2 / CARDS_SIZE, WHITE_TILE / 2 / CARDS_SIZE]
    return result


def _set_glowing(material):
    """
    Rewires a copy of a plant's material for taking its picture: it gives off
    its own base colour, dimmed by the mesh's `sky` attribute, and takes no
    light, so the picture holds the plant's colours and not one moment's sun.
    Leaves count by how much of the pixel they cover rather than being cut
    out, or a crown seen this small goes bald.
    """
    if material.node_tree is None:
        material.use_nodes = True  # Blender < 5: node trees are opt-in
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) or nodes.new("ShaderNodeOutputMaterial")
    glow = nodes.new("ShaderNodeEmission")
    shaded = nodes.new("ShaderNodeVectorMath")  # base colour x the `sky` the picture's mesh carries at each vertex
    shaded.operation = "SCALE"
    base = shader.inputs["Base Color"] if shader is not None else None
    if base is None:
        shaded.inputs[0].default_value = material.diffuse_color[:3]  # some other kind of material: its viewport colour will do
    elif base.is_linked:
        links.new(base.links[0].from_socket, shaded.inputs[0])
    else:
        shaded.inputs[0].default_value = base.default_value[:3]
    sky = nodes.new("ShaderNodeAttribute")
    sky.attribute_name = "sky"
    links.new(sky.outputs["Fac"], shaded.inputs["Scale"])
    links.new(shaded.outputs["Vector"], glow.inputs["Color"])
    surface_out = glow.outputs["Emission"]
    alpha = shader.inputs["Alpha"] if shader is not None else None
    if alpha is not None and alpha.is_linked:
        cover = alpha.links[0].from_socket
        node = cover.node
        while node.type == "MATH":  # back through the cut-out sum to the texture's own alpha
            feeds = [each.links[0].from_node for each in node.inputs if each.is_linked]
            if not feeds:
                break
            node = feeds[0]
        if node.type == "TEX_IMAGE":
            cover = node.outputs["Alpha"]
        # Cards seen nearly edge-on fade out, as the game culls them: they are streaks otherwise.
        view = nodes.new("ShaderNodeNewGeometry")
        facing = nodes.new("ShaderNodeVectorMath")
        facing.operation = "DOT_PRODUCT"
        links.new(view.outputs["True Normal"], facing.inputs[0])
        links.new(view.outputs["Incoming"], facing.inputs[1])
        either = nodes.new("ShaderNodeMath")
        either.operation = "ABSOLUTE"
        links.new(facing.outputs["Value"], either.inputs[0])
        fade = nodes.new("ShaderNodeMapRange")
        fade.inputs["From Min"].default_value, fade.inputs["From Max"].default_value = 0.10, 0.30
        links.new(either.outputs["Value"], fade.inputs["Value"])
        thin = nodes.new("ShaderNodeMath")
        thin.operation = "MULTIPLY"
        links.new(cover, thin.inputs[0])
        links.new(fade.outputs["Result"], thin.inputs[1])
        clear = nodes.new("ShaderNodeBsdfTransparent")
        mix = nodes.new("ShaderNodeMixShader")
        links.new(thin.outputs["Value"], mix.inputs[0])
        links.new(clear.outputs[0], mix.inputs[1])
        links.new(glow.outputs["Emission"], mix.inputs[2])
        surface_out = mix.outputs[0]
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        else:
            material.blend_method = "HASHED"
    links.new(surface_out, output.inputs["Surface"])


def _picture(mesh, points, sky, turn, middle, width, height, columns, rows):
    """
    The plant seen square-on from the side: `columns` x `rows` pixels of
    straight linear colour and coverage, bottom row first, showing `width` x
    `height` metres standing on z = 0 with the trunk `middle` metres left of
    centre. `turn` is the angle (about z) of the direction that runs to the
    right in the picture. `points` is where to put the mesh's vertices for it
    and `sky` the shade to give each. Rendered with Eevee in a scene of its
    own, which is removed again with everything in it.
    """
    made = []  # (collection, datablock), removed in reverse
    window = bpy.context.window
    shown = window.scene if window is not None else None
    had_result = "Render Result" in bpy.data.images
    path = os.path.join(bpy.app.tempdir, f"plant_card_{os.getpid()}.exr")
    try:
        scene = bpy.data.scenes.new("PlantCardPicture")
        made.append((bpy.data.scenes, scene))
        for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = engine
                break
            except TypeError:
                continue
        else:
            raise RuntimeError("this Blender has no Eevee")
        render = scene.render
        render.resolution_x, render.resolution_y, render.resolution_percentage = columns, rows, 100
        render.pixel_aspect_x = render.pixel_aspect_y = 1.0
        render.film_transparent = True
        render.use_compositing = render.use_sequencer = False
        render.use_file_extension = False
        render.filepath = path
        settings = render.image_settings
        if hasattr(settings, "media_type"):
            settings.media_type = "IMAGE"
        settings.file_format = "OPEN_EXR"  # linear, unclipped, no display transform
        settings.color_mode = "RGBA"
        settings.color_depth = "32"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
        scene.eevee.taa_render_samples = CARD_SAMPLES
        for setting in ("use_raytracing", "use_shadows", "use_gtao", "use_bloom", "use_ssr", "use_motion_blur"):
            if hasattr(scene.eevee, setting):
                setattr(scene.eevee, setting, False)

        subject = mesh.copy()  # the plant's own mesh is left as it is
        made.append((bpy.data.meshes, subject))
        subject.vertices.foreach_set("co", np.asarray(points, dtype=np.float32).ravel())
        subject.update()
        subject.attributes.new("sky", "FLOAT", "POINT").data.foreach_set("value", np.asarray(sky, dtype=np.float32))
        for slot, material in enumerate(subject.materials):
            if material is not None:
                glowing = material.copy()
                made.append((bpy.data.materials, glowing))
                _set_glowing(glowing)
                subject.materials[slot] = glowing
        plant = bpy.data.objects.new("PlantCardSubject", subject)
        made.append((bpy.data.objects, plant))
        scene.collection.objects.link(plant)

        lens = bpy.data.cameras.new("PlantCardCamera")
        made.append((bpy.data.cameras, lens))
        lens.type = "ORTHO"
        lens.sensor_fit = "AUTO"
        lens.ortho_scale = max(width, height)
        camera = bpy.data.objects.new("PlantCardCamera", lens)
        made.append((bpy.data.objects, camera))
        scene.collection.objects.link(camera)
        right = np.array([math.cos(turn), math.sin(turn), 0.0])
        back = np.cross(right, UP)
        reach = float(np.hypot(points[:, 0], points[:, 1]).max())
        eye = right * middle + UP * height / 2 + back * (reach + 10.0)
        camera.matrix_world = Matrix([  # its x to the picture's right, its y up, looking along -z
            (right[0], UP[0], back[0], eye[0]),
            (right[1], UP[1], back[1], eye[1]),
            (right[2], UP[2], back[2], eye[2]),
            (0.0, 0.0, 0.0, 1.0),
        ])
        lens.clip_start, lens.clip_end = 1.0, 2.0 * reach + 20.0
        scene.camera = camera

        bpy.ops.render.render(write_still=True, scene=scene.name)
        if not os.path.exists(path):
            raise RuntimeError("the render wrote no picture")
        loaded = bpy.data.images.load(path, check_existing=False)
        made.append((bpy.data.images, loaded))
        if tuple(loaded.size) != (columns, rows):
            raise RuntimeError("the render came back the wrong size")
        pixels = np.empty(columns * rows * 4, dtype=np.float32)
        loaded.pixels.foreach_get(pixels)
    finally:
        for collection, block in reversed(made):
            try:
                collection.remove(block)
            except (ReferenceError, RuntimeError):
                pass
        if not had_result and "Render Result" in bpy.data.images:
            bpy.data.images.remove(bpy.data.images["Render Result"])
        if shown is not None and window.scene != shown:
            window.scene = shown
        if os.path.exists(path):
            os.remove(path)
    pixels = pixels.reshape(rows, columns, 4)
    cover = np.clip(pixels[..., 3], 0.0, 1.0)
    return pixels[..., :3] / np.maximum(cover, 1e-4)[..., None], cover  # the file holds colour x coverage


def _open_sky(mesh, points):
    """
    How much of an overcast sky each vertex sees past the rest of the plant,
    as the shade to paint it in (CARD_SHADE where it sees none, 1 in the
    open). The sky is taken in five parts, straight overhead and four
    quarters round the horizon; the faces that lie in a part hide it by
    their size and nearness. This is the picture's only shading: the top of
    each clump of leaves stays light, what hangs under it darkens, the trunk
    below the crown keeps its light from the side, and the plant reads as a
    volume without any one sun being baked in.
    """
    count = len(mesh.polygons)
    centres = np.empty(count * 3, dtype=np.float32)
    mesh.polygons.foreach_get("center", centres)
    centres = centres.reshape(-1, 3)
    areas = np.empty(count, dtype=np.float32)
    mesh.polygons.foreach_get("area", areas)
    slots = np.empty(count, dtype=np.int32)
    mesh.polygons.foreach_get("material_index", slots)
    leafy = np.array([bool(m is not None and m.get("foliage")) for m in mesh.materials] or [False])
    # A leaf card is mostly holes, and lies any way up: it hides its covered area, halved.
    solid = [0.5 * float(_atlas(m.name)[0]["coverage"]) if leaf and (m.name in LEAVES or m.name in NEEDLES) else 1.0 for m, leaf in zip(mesh.materials, leafy)]
    hides = areas * np.asarray(solid or [1.0], dtype=np.float32)[slots] * CARD_DEPTH
    near = areas * 0.25  # a face counts as no nearer than half its own size
    points = np.asarray(points, dtype=np.float32)
    # What each part of the sky is worth, and its size in steradians: overhead is within 45 degrees of straight up.
    worth = np.array([0.5, 0.125, 0.125, 0.125, 0.125], dtype=np.float32)
    extent = np.array([math.tau * (1.0 - math.sqrt(0.5))] + [math.tau * math.sqrt(0.5) / 4] * 4, dtype=np.float32)
    sky = np.empty(len(points), dtype=np.float32)
    step = 512
    for start in range(0, len(points), step):
        to = centres[None, :, :] - points[start:start + step, None, :]
        rows = to.shape[0]
        distance = np.einsum("ijk,ijk->ij", to, to)
        flat = distance - to[..., 2] ** 2  # squared distance on the ground
        part = np.where(to[..., 2] ** 2 > flat, 0, 1 + (to[..., 0] > 0) + 2 * (to[..., 1] > 0))
        part += (np.arange(rows) * 5)[:, None]
        hidden = np.where(to[..., 2] > 0.0, hides / np.maximum(distance, near), 0.0)
        depth = np.bincount(part.ravel(), weights=hidden.ravel(), minlength=rows * 5).reshape(rows, 5)
        sky[start:start + step] = (np.exp(-depth / extent) * worth).sum(axis=1)
    # Leaves pass some light on and wood none: a trunk inside the crown goes twice as dark as the leaves around it.
    corners = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", corners)
    sizes = np.empty(count, dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    darkest = np.full(len(points), CARD_SHADE, dtype=np.float32)
    darkest[corners[~np.repeat(leafy[slots], sizes)]] = CARD_SHADE * 0.5
    return darkest + (1.0 - darkest) * sky


def _spread(rgb, filled, passes=24):
    """Carries colour outward from the `filled` pixels into the rest, as _Canvas.bleed does, on a picture of any shape."""
    rgb = np.where(filled[..., None], rgb, 0.0).astype(np.float32)
    weight = filled.astype(np.float32)
    mean = rgb[filled].mean(axis=0)
    for _ in range(passes):
        if weight.all():
            break
        total = np.zeros_like(rgb)
        count = np.zeros_like(weight)
        colour = np.pad(rgb * weight[..., None], ((1, 1), (1, 1), (0, 0)))
        known = np.pad(weight, 1)
        rows, columns = weight.shape
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                total += colour[dy:dy + rows, dx:dx + columns]
                count += known[dy:dy + rows, dx:dx + columns]
        grow = (weight == 0) & (count > 0)
        rgb[grow] = total[grow] / count[grow][:, None]
        weight[grow] = 1.0
    rgb[weight == 0] = mean
    return rgb


def _impostor(name, mesh, foliage, planes):
    """
    The stand-in for `mesh`: `planes` cards crossed on the trunk, showing its
    picture, which is rendered here into a tile of the shared atlas. Returns
    (mesh, triangles).
    """
    points = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", points)
    points = points.reshape(-1, 3).astype(np.float64)
    # Every card shows the one picture, so from some sides the plant is seen
    # next to its own mirror image, and whatever is lopsided in the picture
    # shows double: a leaning trunk as a fork. So the lean is taken out of the
    # picture (the plant is stood up straight for it) and put into the cards,
    # which lean as the plant does, from its foot.
    rise = np.maximum(points[:, 2], 0.0)
    lean = (points[:, :2] * rise[:, None]).sum(axis=0) / max(float((rise * rise).sum()), 1e-9)  # metres sideways per metre up
    lean *= min(1.0, CARD_LEAN / max(float(np.hypot(*lean)), 1e-9))
    upright = points.copy()
    upright[:, :2] -= lean * rise[:, None]
    # And it is taken square-on to the plant's broadest side: seen from elsewhere the cards show
    # it narrower than it is (by an eighth at worst, with three), never wider than the plant ever is.
    spread = upright[:, :2] - upright[:, :2].mean(axis=0)
    broad = np.linalg.eigh(spread.T @ spread)[1][:, -1]
    turn = math.atan2(broad[1], broad[0]) % math.pi  # an axis has no sign: always the same one of the two
    across = upright[:, 0] * math.cos(turn) + upright[:, 1] * math.sin(turn)
    low, high, top = float(across.min()), float(across.max()), float(points[:, 2].max())
    if top <= 0.0 or high <= low:
        raise RuntimeError(f"{name} has no size")

    # A pixel and a half to spare at the sides and the top; the foot of the picture is the ground.
    longest = CARD_PIXELS[0] if max(high - low, top) > 4.0 else CARD_PIXELS[1]
    scale = (longest - 3.0) / max(high - low, top)  # pixels per metre
    columns = min(longest, 4 * math.ceil(((high - low) * scale + 3.0) / 4))
    rows = min(longest, 4 * math.ceil((top * scale + 1.5) / 4))
    width, height = columns / scale, rows / scale
    middle = (low + high) / 2

    rgb, cover = _picture(mesh, upright, _open_sky(mesh, points), turn, middle, width, height, columns, rows)
    # A spray seen this small covers a part of each pixel; where the game's cut-off falls decides how leafy the outline is.
    alpha = np.clip(cover * (ALPHA_CUTOFF / CARD_COVER), 0.0, 1.0)
    solid = alpha >= ALPHA_CUTOFF
    if solid.mean() < 0.02:
        raise RuntimeError(f"the picture of {name} came out empty")

    # The game cannot shade a flat card with itself: the picture is that much darker than the leaves.
    average = np.asarray(_atlas(foliage)[0]["average"], dtype=np.float64)
    weights = np.array([0.2126, 0.7152, 0.0722])
    rgb = np.clip(rgb * IMPOSTOR_GAIN * float(average @ weights) / max(float(rgb[solid].mean(axis=0) @ weights), 1e-6), 0.0, 1.0)
    rgb = _spread(rgb, cover > 0.25)

    # Into the atlas, with a margin that repeats the picture's edge (the trunk runs on down through it).
    image = _cards_image()
    material = cards_material()
    margin = CARD_MARGIN
    tile = np.pad(np.concatenate([_srgb(rgb), alpha[..., None]], axis=-1), ((margin, margin), (margin, margin), (0, 0)), mode="edge").astype(np.float32)
    tiles = _card_tiles(image)
    size = (columns + 2 * margin, rows + 2 * margin)
    mine = next((each for each in tiles if each[0] == name and tuple(each[3:5]) == size), None)  # built before in this file: the same picture
    if mine is None:
        spot = _tile_spots([each[3:5] for each in tiles] + [size])[-1]
        if spot is None:
            raise _AtlasFull(f"the {CARDS_SIZE} px atlas of plant pictures is full")
        mine = [name, spot[0], spot[1], size[0], size[1]]
        tiles.append(mine)
    x, y = mine[1], mine[2]
    pixels = np.empty(CARDS_SIZE * CARDS_SIZE * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(CARDS_SIZE, CARDS_SIZE, 4)
    pixels[y:y + size[1], x:x + size[0]] = tile
    image.pixels.foreach_set(pixels.ravel())
    image.pack()
    image["tiles"] = json.dumps(tiles)

    # The cards: one in the picture's own plane and the rest fanned evenly about it, each showing the whole picture, leaning with the plant.
    u0, u1 = (x + margin) / CARDS_SIZE, (x + margin + columns) / CARDS_SIZE
    v0, v1 = (y + margin) / CARDS_SIZE, (y + margin + rows) / CARDS_SIZE
    left, right = middle - width / 2, middle + width / 2
    reach = max(abs(left), abs(right))
    vertices, faces, uvs, normals = [], [], [], []
    for plane in range(planes):
        angle = turn + math.pi / planes * (plane - (planes - 1) // 2)
        along = np.array([math.cos(angle), math.sin(angle), 0.0])
        faces.append(tuple(range(len(vertices), len(vertices) + 4)))
        for s, z, u, v in ((left, 0.0, u0, v0), (right, 0.0, u1, v0), (right, height, u1, v1), (left, height, u0, v1)):
            vertices.append((along[0] * s + lean[0] * z, along[1] * s + lean[1] * z, z))
            uvs.append((u, v))
            normals.append(tuple(_unit(along * CARD_NORMAL[0] * s / reach + UP * CARD_NORMAL[1])))  # out from the trunk and up, the same seen from either side
    card = bpy.data.meshes.new(f"{name}_far")
    card.from_pydata(vertices, [], faces)
    card.uv_layers.new(name="UVMap", do_init=False).data.foreach_set("uv", np.asarray(uvs, dtype=np.float32).ravel())
    card.uv_layers.active = card.uv_layers["UVMap"]
    card.uv_layers["UVMap"].active_render = True
    card.polygons.foreach_set("use_smooth", np.ones(len(faces), dtype=bool))
    card.materials.append(material)
    card.use_fake_user = True  # a stand-in is on no object, and must still be saved with the file
    card.update()
    card.normals_split_custom_set(normals)
    card.update()
    return card, 2 * planes


class _AtlasFull(RuntimeError):
    pass


_cards_off = False  # set once a picture has failed for a reason that will not go away
_cards_told = set()


def _stand_in(name, mesh, foliage, planes, lump):
    """
    A plant's distant version, as (mesh, triangles): impostor cards, or the
    plain shape `lump()` builds if no picture can be had (no Eevee, no
    graphics, the atlas full).
    """
    global _cards_off
    if IMPOSTORS and not _cards_off:
        try:
            return _impostor(name, mesh, foliage, planes)
        except _AtlasFull as error:
            if "full" not in _cards_told:
                _cards_told.add("full")
                print(f"plants: {error}; {name} and every tree or bush after it that finds no room gets a plain lump as its far mesh")
        except Exception as error:  # whatever went wrong, the level must still build
            _cards_off = True  # not worth trying again for every plant
            print(f"plants: no impostor cards ({type(error).__name__}: {error}); from {name} on, trees and bushes get plain lumps as their far meshes")
    return lump()


# --- Skeletons ---------------------------------------------------------------------


class _Limb:
    def __init__(self, points, radii, level, phase, distance):
        self.points = np.asarray(points, dtype=np.float64)
        self.radii = np.asarray(radii, dtype=np.float64)
        self.level = level
        self.phase = phase
        steps = np.linalg.norm(np.diff(self.points, axis=0), axis=1)
        self.along = np.concatenate([[0.0], np.cumsum(steps)])  # metres from this limb's start
        self.distance = distance  # metres of wood between the trunk and this limb's start
        self.length = float(self.along[-1])

    def at(self, t):
        """Position, direction, radius and metres along, a fraction t of the way out."""
        s = min(max(t, 0.0), 1.0) * self.length
        i = int(min(np.searchsorted(self.along, s, side="right") - 1, len(self.points) - 2))
        f = (s - self.along[i]) / max(self.along[i + 1] - self.along[i], 1e-9)
        position = self.points[i] + (self.points[i + 1] - self.points[i]) * f
        direction = _unit(self.points[i + 1] - self.points[i])
        radius = self.radii[i] + (self.radii[i + 1] - self.radii[i]) * f
        return position, direction, radius, s


def _curve(rng, start, heading, end, end_heading, count, crook):
    """
    A limb's centre line from `start` to `end`: a smooth bend (leaving along
    `heading`, arriving along `end_heading`) knocked crooked by `crook`, a
    fraction of its length.
    """
    start, end = np.asarray(start, dtype=np.float64), np.asarray(end, dtype=np.float64)
    span = np.linalg.norm(end - start)
    c1 = start + _unit(heading) * span * 0.38
    c2 = end - _unit(end_heading) * span * 0.30
    points = []
    drift = np.zeros(3)
    for i in range(count + 1):
        t = i / count
        p = (1 - t) ** 3 * start + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t**2 * c2 + t**3 * end
        if 0 < i:
            drift = drift * 0.5 + _random_unit(rng) * crook * span
            p = p + drift * math.sin(math.pi * min(t, 0.97) ** 0.8) ** 0.5
        points.append(p)
    return points


def _taper(count, start, end, power=1.0):
    return [end + (start - end) * (1.0 - i / count) ** power for i in range(count + 1)]


def _add_limbs(geometry, limbs, sides, slot, sway, metres):
    """Tubes for limbs; `sides[level]` of 0 leaves that level to the leaf cards. Returns the triangles spent."""
    before = geometry.triangles
    for limb in limbs:
        around = sides[min(limb.level, len(sides) - 1)]
        if around < 3:
            continue
        winds = [sway(p, limb.distance + s) for p, s in zip(limb.points, limb.along)]
        geometry.tube(limb.points, limb.radii, around, winds, limb.phase, slot, metres=metres, v_start=limb.phase * 7.0)
    return geometry.triangles - before


def _card(geometry, base, direction, side, length, width, tile, flip, slot, wind_base, phase, normal_at, color_base, color_tip):
    """
    One foliage card: a quad from `base` along `direction`, `width` across
    along `side`, showing atlas tile 0..3 (mirrored if `flip`). Sway runs from
    `wind_base` where it joins the plant to 1 at its tip, colour likewise from
    `color_base` to `color_tip`.
    """
    u0, v0 = (tile % 2) * 0.5, (tile // 2) * 0.5
    ua, ub = (u0 + 0.5, u0) if flip else (u0, u0 + 0.5)
    half = np.asarray(side) * width / 2
    tip = base + direction * length
    corners = [base - half, base + half, tip + half, tip - half]
    geometry.face(
        corners,
        [(ua, v0), (ub, v0), (ub, v0 + 0.5), (ua, v0 + 0.5)],
        [(wind_base, phase), (wind_base, phase), (1.0, phase), (1.0, phase)],
        [tuple(normal_at(c)) for c in corners],
        [tuple(color_base), tuple(color_base), tuple(color_tip), tuple(color_tip)],
        slot,
    )


def _spray(rng, outward, along, droop=0.0, flat=0.72, upright=0.4):
    """
    How a leaf spray at a spot on a plant's surface is turned. `outward` is the
    way out of the plant there, `along` the way its branch is going. Most
    (`flat` of them) lie on the surface facing out of it, which is how they
    are seen by anyone who can see them at all; the rest stand out from it as
    two crossed cards, and make the leafy outline. Returns (aim, sides): the
    direction the spray grows in, and one side vector per card.
    """
    if rng.random() < flat:
        face = outward + _random_unit(rng) * 0.55
        if face[2] > 0:
            face[2] *= upright  # on top they stand more upright, or from the ground they would all be seen edge-on
        face = _unit(face)
        aim = along * rng.uniform(0.2, 0.9) + _random_unit(rng) * 0.9 + UP * (rng.uniform(-0.3, 0.3) - droop)
        aim = _unit(aim - face * float(np.dot(aim, face)))
        return aim, [_unit(np.cross(aim, face))]
    aim = _unit(along * rng.uniform(0.3, 1.0) + outward * rng.uniform(0.3, 0.9) + _random_unit(rng) * 0.6 + UP * (rng.uniform(-0.1, 0.4) - droop))
    side = _unit(np.cross(aim, _random_unit(rng)))
    return aim, [side, np.cross(aim, side)]


def _card_tone(rng, shade):
    """A card's colour multiplier: `shade` (its place in the crown) with a little hue and tone of its own."""
    warm = rng.uniform(-1.0, 1.0)
    tone = np.array([1.0 + 0.10 * warm, 1.0, 1.0 - 0.16 * warm]) * rng.uniform(0.82, 1.0) * shade
    return tone / max(1.0, float(tone.max()))


# --- Broadleaf trees ---------------------------------------------------------------

BROADLEAF = {
    # A spreading oak: short stout trunk forking low, wide dome wider than it is tall.
    "oak": dict(
        material="LeavesOak", far="FarLeavesOak", height=(8.0, 10.5), trunk=0.034, clear=(0.20, 0.27), fork=0.50,
        crown=(0.47, 0.36), crown_z=0.60, limbs=(7, 8), limb_lift=(0.15, 0.75), branches=5, crook=0.045, lean=0.03,
        lumps=0.22, card=1.4, droop=0.0, low=-0.45,
    ),
    # A tall ash: straight trunk carried well up, steep limbs, a narrow open crown of drooping compound leaves.
    "ash": dict(
        material="LeavesAsh", far="FarLeavesAsh", height=(9.5, 11.0), trunk=0.027, clear=(0.20, 0.27), fork=0.82,
        crown=(0.215, 0.41), crown_z=0.60, limbs=(9, 11), limb_lift=(0.7, 1.3), branches=4, crook=0.03, lean=0.015,
        lumps=0.24, card=1.3, droop=0.25, low=-0.8,
    ),
    # A small crooked hawthorn: leaning, twisted trunk, a dense lopsided flat-topped crown.
    "thorn": dict(
        material="LeavesThorn", far="FarLeavesThorn", height=(6.0, 7.2), trunk=0.031, clear=(0.24, 0.32), fork=0.52,
        crown=(0.40, 0.27), crown_z=0.66, limbs=(6, 7), limb_lift=(0.1, 0.6), branches=5, crook=0.085, lean=0.16,
        lumps=0.35, card=1.1, droop=0.0, low=-0.35,
    ),
}
_BROADLEAF_ORDER = ["oak", "ash", "thorn"]

_cache = {}


def _cached(key):
    plant = _cache.get(key)
    if plant is None:
        return None
    try:  # a new file may have been started since: the old meshes are gone
        if plant.mesh.name in bpy.data.meshes and plant.far.name in bpy.data.meshes:
            return plant
    except ReferenceError:
        pass
    del _cache[key]
    return None


def _variant(variant, order):
    if isinstance(variant, str):
        if variant not in order:
            raise ValueError(f"unknown variant '{variant}'; choose from {order}")
        return variant
    return order[int(variant) % len(order)]


def broadleaf_tree(variant=0, seed=0, bark=None):
    """
    A broadleaf tree, 6 to 11 m: variant 0 / "oak" (spreading), 1 / "ash"
    (tall and narrow), 2 / "thorn" (small and crooked).
    """
    kind = _variant(variant, _BROADLEAF_ORDER)
    key = ("broadleaf", kind, int(seed))
    plant = _cached(key)
    if plant is not None:
        return plant
    spec = BROADLEAF[kind]
    rng = random.Random(1000003 * (_BROADLEAF_ORDER.index(kind) + 1) + int(seed) * 7919)
    bark = bark or bark_material()
    leaves = foliage_material(spec["material"])
    metres = float(bark.get("metres", 1.0))

    height = rng.uniform(*spec["height"])
    trunk_radius = height * spec["trunk"] * rng.uniform(0.9, 1.1)
    clear = height * rng.uniform(*spec["clear"])
    fork = height * spec["fork"]
    size = spec["card"]
    # The crown as asked for includes its leaves; the wood stops short of it by most of a card.
    crown_r = height * spec["crown"][0] * rng.uniform(0.92, 1.08) - size * 0.35
    crown_h = height * spec["crown"][1] - size * 0.3
    lean = _unit([rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0]) * spec["lean"] * rng.uniform(0.5, 1.2)
    centre = np.array([lean[0] * height * 1.1, lean[1] * height * 1.1, height * spec["crown_z"]])
    radii = np.array([crown_r, crown_r * rng.uniform(0.85, 1.0), crown_h])
    top = centre[2] + crown_h
    scale = (height - size * 1.35) / top  # so the tree, leaves and all, comes out about the height asked for
    centre[2] *= scale
    radii[2] *= scale
    fork *= scale
    clear *= scale

    # The crown is an ellipsoid with a few bulges and hollows, so no two sides match.
    bumps = [(_random_unit(rng), rng.uniform(-1.0, 1.0) * spec["lumps"]) for _ in range(7)]

    def envelope(direction):
        """How far the crown reaches from its centre in a direction (unit vector)."""
        swell = 1.0 + sum(amount * max(0.0, float(np.dot(direction, axis))) ** 3 for axis, amount in bumps)
        return direction * radii * swell

    # Trunk: up to the fork, then on as the leading limb to the top of the crown.
    count = 9
    points = []
    drift = np.zeros(3)
    veer = _unit([rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0]) * crown_r * rng.uniform(0.1, 0.3)
    peak = centre + envelope(_unit([veer[0] / crown_r, veer[1] / crown_r, 1.6])) * 0.9
    for i in range(count + 1):
        t = i / count
        z = -0.35 + (peak[2] + 0.35) * t
        above = _smoothstep(fork * 0.7, peak[2], z)
        drift = drift * 0.6 + np.array([rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0]) * spec["crook"] * height * 0.32 * (i > 1)
        xy = lean[:2] * max(z, 0.0) * 1.1 * (1.0 - above) + (peak[:2]) * above + drift[:2] * math.sin(math.pi * t**0.7)
        points.append(np.array([xy[0], xy[1], z]))
    trunk_radii = []
    for p in points:
        z = max(p[2], 0.0)
        r = trunk_radius * (1.0 - 0.28 * min(z / fork, 1.0))  # slow taper up to the fork
        if z > fork:
            r *= (1.0 - (z - fork) / (peak[2] - fork)) ** 1.1 * 0.8 + 0.02
        r *= 1.0 + 0.55 * math.exp(-z / (trunk_radius * 1.8))  # flaring into the roots
        trunk_radii.append(max(r, 0.015))
    trunk = _Limb(points, trunk_radii, 0, 0.0, 0.0)
    limbs = [trunk]

    def trunk_at(z):
        t = np.interp(z, trunk.points[:, 2], np.linspace(0.0, 1.0, len(trunk.points)))
        i = min(int(t * count), count - 1)
        f = t * count - i
        return trunk.points[i] + (trunk.points[i + 1] - trunk.points[i]) * f, trunk.radii[i] + (trunk.radii[i + 1] - trunk.radii[i]) * f

    # Main limbs: each is sent to its own patch of the crown's surface, spread
    # evenly (a sunflower spiral), the lowest patches from lowest on the trunk.
    limb_count = rng.randint(*spec["limbs"])
    turn = rng.uniform(0.0, math.tau)
    targets = []
    for i in range(limb_count):
        h = spec["low"] + (0.80 - spec["low"]) * (i + 0.5) / limb_count  # height on the unit ball
        azimuth = turn + i * 2.39996 + rng.uniform(-0.25, 0.25)
        r = math.sqrt(max(0.0, 1.0 - h * h))
        targets.append(_unit([r * math.cos(azimuth), r * math.sin(azimuth), h]))
    targets.sort(key=lambda d: d[2])
    main = []
    for i, direction in enumerate(targets):
        end = centre + envelope(direction) * rng.uniform(0.78, 0.9)
        z = clear + (fork * 1.08 - clear) * ((i + rng.uniform(0.2, 0.8)) / limb_count) ** 0.8
        z = min(z, end[2] - 0.3) if end[2] > clear + 0.4 else z
        start, parent_radius = trunk_at(max(z, clear * 0.9))
        outward = _unit([end[0] - start[0], end[1] - start[1], 0.0])
        lift = rng.uniform(*spec["limb_lift"])
        heading = _unit(outward + UP * lift)
        arrive = _unit(outward * 0.7 + UP * (0.45 + 0.5 * max(direction[2], 0.0)))
        path = _curve(rng, start, heading, end, arrive, 5, spec["crook"])
        base_radius = min(parent_radius * rng.uniform(0.46, 0.6), trunk_radius * 0.5)
        limb = _Limb(path, _taper(5, base_radius, 0.02, 0.85), 1, rng.random(), 0.0)
        limbs.append(limb)
        main.append(limb)

    # Branches off the limbs (and off the leader above the fork).
    branches = []
    carriers = [(limb, 0.28) for limb in main] + [(trunk, float(np.interp(fork, trunk.points[:, 2], np.linspace(0, 1, count + 1))) + 0.08)]
    for limb, first in carriers:
        number = spec["branches"] + rng.choice((-1, 0, 0, 1))
        if limb is trunk:
            number = max(3, number - 1)
        spin = rng.uniform(0.0, math.tau)
        for j in range(number):
            t = first + (0.93 - first) * (j + rng.uniform(0.25, 0.75)) / number
            start, direction, radius, s = limb.at(t)
            outward = _unit(start - centre)
            side = _unit(np.cross(direction, UP))
            around = spin + j * 2.4
            heading = _unit(direction * 0.55 + side * math.cos(around) * 0.9 + np.cross(side, direction) * math.sin(around) * 0.5 + outward * 0.45 + UP * 0.25)
            reach = np.linalg.norm(envelope(outward)) - np.linalg.norm((start - centre))  # room left before the crown's edge
            length = max(0.8, min(limb.length * rng.uniform(0.3, 0.45) * (1.15 - 0.5 * t), max(reach, 0.0) + rng.uniform(0.6, 1.2)))
            end = start + heading * length
            arrive = _unit(heading + UP * (0.35 - spec["droop"]) + outward * 0.3)
            path = _curve(rng, start, heading, end, arrive, 3, spec["crook"] * 1.5)
            phase = (limb.phase + rng.uniform(-0.08, 0.08)) % 1.0 if limb is not trunk else rng.random()
            branch = _Limb(path, _taper(3, max(radius * 0.5, 0.028), 0.012), 2, phase, limb.distance + s)
            limbs.append(branch)
            branches.append(branch)

    furthest = max(limb.distance + limb.length for limb in limbs if limb is not trunk) + spec["card"]

    def sway(point, distance):
        return float(min(1.0, 0.16 * (max(point[2], 0.0) / height) ** 1.6 + 0.62 * distance / furthest))

    geometry = _Geometry()
    geometry.tube(trunk.points, trunk.radii, 8, [sway(p, 0.0) for p in trunk.points], 0.0, 0, metres=metres)
    _add_limbs(geometry, limbs[1:], [0, 5, 3], 0, sway, metres)

    # Foliage: sprays along the branches and the outer ends of the limbs, by length.
    normal_centre = centre - np.array([0.0, 0.0, radii[2] * 0.35])

    def normal_at(point):
        return _unit(_unit((point - normal_centre) / radii) * 0.8 + UP * 0.3)

    def shade_at(point):
        depth = np.linalg.norm((point - centre) / radii)  # 0 at the heart of the crown, about 1 at its surface
        low = _smoothstep(-1.0, 0.3, (point[2] - centre[2]) / radii[2])
        return (0.50 + 0.50 * _smoothstep(0.35, 1.0, depth)) * (0.80 + 0.20 * low)

    runs = [(branch, 0.2, 1.0) for branch in branches] + [(limb, 0.6, 1.0) for limb in main] + [(trunk, 0.82, 1.0)]
    total = sum(limb.length * (t1 - t0) for limb, t0, t1 in runs)
    cards = (TREE_BUDGET - geometry.triangles) // 2
    centres = []
    for limb, t0, t1 in runs:
        share = max(2, int(round(cards / 1.28 * limb.length * (t1 - t0) / total - 0.35)))  # 28% of sprays are two cards
        for k in range(share):
            at_tip = k >= share - 2
            t = 1.0 if at_tip else t0 + (t1 - t0) * (k + rng.uniform(0.1, 0.9)) / max(share - 2, 1)
            base, direction, _, s = limb.at(t)
            if not at_tip:
                base = base + _random_unit(rng) * rng.uniform(0.0, 0.35)  # on a twig of its own, not all in a row
            outward = _unit(base - centre)
            aim, sides = _spray(rng, outward, direction, spec["droop"])
            length = size * rng.uniform(0.8, 1.2)
            wind_base = sway(base, limb.distance + s)
            tip_point = base + aim * length
            base_tone = _card_tone(rng, shade_at(base + aim * length * 0.2))
            tip_tone = base_tone * (shade_at(tip_point) / max(shade_at(base + aim * length * 0.2), 1e-3)) ** 0.5
            phase = (limb.phase + rng.uniform(-0.05, 0.05)) % 1.0
            for across in sides:
                _card(
                    geometry, base - aim * length * 0.04, aim, across, length, length, rng.randrange(4), rng.random() < 0.5, 1,
                    wind_base, phase, normal_at, base_tone * 0.9, np.minimum(tip_tone, 1.0),
                )
            centres.append(base + aim * length * 0.55)
            if geometry.triangles >= TREE_BUDGET - 3:
                break
        if geometry.triangles >= TREE_BUDGET - 3:
            break

    # Leaves stand a little above the wood, by how much depends on how they fell: bring the whole tree to the height meant.
    fit = height / max(v[2] for v in geometry.vertices)
    geometry.vertices = [(v[0] * fit, v[1] * fit, v[2] * fit) for v in geometry.vertices]
    name = f"Tree{kind.capitalize()}_{int(seed)}"
    mesh = geometry.to_mesh(name, [bark, leaves])

    centres = np.array(centres)

    def lump():
        """The stand-in without a picture: a trunk and a leaf-coloured skin pulled over the foliage."""
        far = _Geometry()
        far_points = [trunk.at(t)[0] for t in (0.0, 0.3, 0.62)]
        far_radii = [trunk.radii[0] * 0.9, trunk.at(0.3)[2], trunk.at(0.62)[2]]
        far.tube(far_points, far_radii, 5, [sway(p, 0.0) for p in far_points], 0.0, 0, tip=False)
        _add_shell(far, centres, centre, radii, size * 0.1, 1, (sway(centre, furthest * 0.8), 0.5))
        far.vertices = [(v[0] * fit, v[1] * fit, min(v[2] * fit, height)) for v in far.vertices]
        materials = [flat_material("FarBark", (0.085, 0.06, 0.04), roughness=0.95), far_material(spec["far"], spec["material"])]
        return far.to_mesh(f"{name}_far", materials, colors=False), far.triangles

    far_mesh, far_triangles = _stand_in(name, mesh, spec["material"], 3, lump)

    spread = np.linalg.norm(centres[:, :2] - centre[:2], axis=1)
    plant = Plant(
        mesh=mesh, far=far_mesh, height=float(height), trunk_radius=float(trunk_radius * fit), crown_radius=float((np.percentile(spread, 90) + size * 0.4) * fit),
        triangles=geometry.triangles, far_triangles=far_triangles, kind=kind, seed=int(seed),
    )
    _cache[key] = plant
    return plant


# --- Conifers ------------------------------------------------------------------------

CONIFERS = {
    # A Norway spruce: wide skirts, boughs that sag and turn up at the tip, fronds hanging from them.
    "spruce": dict(
        material="NeedlesSpruce", far="FarNeedlesSpruce", height=(10.5, 14.0), trunk=0.021, clear=(0.10, 0.17), spread=0.215,
        whorl=(0.60, 0.36), per_whorl=(5, 6), rise=(-0.30, 0.80), sag=0.55, upturn=0.55, frond=1.45, hang=0.75,
    ),
    # A fir: narrower and neater, stiff boughs angled up, fronds held flat.
    "fir": dict(
        material="NeedlesSpruce", far="FarNeedlesSpruce", height=(7.5, 10.5), trunk=0.019, clear=(0.07, 0.13), spread=0.165,
        whorl=(0.36, 0.22), per_whorl=(5, 6), rise=(0.05, 0.75), sag=0.22, upturn=0.3, frond=1.05, hang=0.32,
    ),
}
_CONIFER_ORDER = ["spruce", "fir"]


def conifer_tree(variant=0, seed=0, bark=None):
    """A conifer, 7 to 14 m: variant 0 / "spruce" (broad, drooping tiers), 1 / "fir" (narrow, upswept)."""
    kind = _variant(variant, _CONIFER_ORDER)
    key = ("conifer", kind, int(seed))
    plant = _cached(key)
    if plant is not None:
        return plant
    spec = CONIFERS[kind]
    rng = random.Random(2000003 * (_CONIFER_ORDER.index(kind) + 1) + int(seed) * 7919)
    bark = bark or bark_material()
    needles = foliage_material(spec["material"])
    metres = float(bark.get("metres", 1.0))

    height = rng.uniform(*spec["height"])
    trunk_radius = height * spec["trunk"] * rng.uniform(0.92, 1.08) + 0.03
    base = height * rng.uniform(*spec["clear"])  # where the green starts
    spread = height * spec["spread"] * rng.uniform(0.9, 1.1)
    top = height - 0.35  # the leader's frond stands above the last of the wood

    count = 8
    points, trunk_radii = [], []
    bow = _unit([rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0]) * height * rng.uniform(0.0, 0.012)
    for i in range(count + 1):
        t = i / count
        z = -0.35 + (top + 0.35) * t**1.15
        points.append(np.array([bow[0] * math.sin(math.pi * t), bow[1] * math.sin(math.pi * t), z]))
        zz = max(z, 0.0)
        trunk_radii.append(max(trunk_radius * (1.0 - zz / top) ** 0.85 * (1.0 + 0.5 * math.exp(-zz / (trunk_radius * 1.6))), 0.012))
    trunk = _Limb(points, trunk_radii, 0, 0.0, 0.0)

    def trunk_at(z):
        x = np.interp(z, trunk.points[:, 2], trunk.points[:, 0])
        y = np.interp(z, trunk.points[:, 2], trunk.points[:, 1])
        return np.array([x, y, z]), float(np.interp(z, trunk.points[:, 2], trunk.radii))

    def reach_at(z):
        """How far the boughs reach at a height: widest just above the lowest, a point at the top."""
        rel = (z - base) / (height - base)
        return spread * (1.0 - rel) ** 0.9 * (0.80 + 0.20 * _smoothstep(0.0, 0.14, rel)) + 0.22

    # Whorls of boughs up the trunk, closer together toward the top.
    boughs = []
    z = base
    turn = rng.uniform(0.0, math.tau)
    while z < height - 0.55:
        rel = (z - base) / (height - base)
        number = rng.randint(*spec["per_whorl"]) if rel < 0.85 else 4
        turn += rng.uniform(0.5, 0.9)
        for k in range(number):
            azimuth = turn + math.tau * k / number + rng.uniform(-0.2, 0.2)
            zk = z + rng.uniform(-0.17, 0.17)
            start, parent_radius = trunk_at(zk)
            length = reach_at(zk) * rng.uniform(0.78, 1.08)
            if rng.random() < 0.1:
                length *= 0.6  # a broken or shaded-out bough now and then
            out = np.array([math.cos(azimuth), math.sin(azimuth), 0.0])
            rise = spec["rise"][0] + (spec["rise"][1] - spec["rise"][0]) * rel**1.3 + rng.uniform(-0.16, 0.16)
            segments = 4
            path = [start]
            for i in range(segments):
                t = (i + 0.5) / segments
                slope = rise - spec["sag"] * (0.55 * (1.0 - (2.0 * t - 1.0) ** 2) + 0.45 * t) * (1.0 - 0.6 * rel) + spec["upturn"] * t**3
                path.append(path[-1] + (out * math.cos(slope) + UP * math.sin(slope)) * length / segments)
            radius = min(parent_radius * 0.5, 0.012 + length * 0.016)
            boughs.append(_Limb(path, _taper(segments, radius, 0.008), 1, rng.random(), 0.0))
        z += spec["whorl"][0] + (spec["whorl"][1] - spec["whorl"][0]) * rel

    furthest = max(bough.length for bough in boughs) + spec["frond"]

    def sway(point, distance):
        return float(min(1.0, 0.20 * (max(point[2], 0.0) / height) ** 1.8 + 0.58 * distance / furthest))

    geometry = _Geometry()
    geometry.tube(trunk.points, trunk.radii, 6, [sway(p, 0.0) for p in trunk.points], 0.0, 0, metres=metres)
    # Wood for the longer boughs only; the short ones near the top are hidden in their fronds.
    woody = sorted(boughs, key=lambda b: -b.length)[: int(len(boughs) * 0.4)]
    for bough in woody:
        thin = _Limb(bough.points[[0, 1, 3]], bough.radii[[0, 1, 4]], 1, bough.phase, 0.0)
        geometry.tube(thin.points, thin.radii, 3, [sway(p, s) for p, s in zip(thin.points, thin.along)], bough.phase, 0, metres=metres, v_start=bough.phase * 5.0)

    def normal_at(point):
        axis = trunk_at(point[2])[0]
        return _unit(_unit([point[0] - axis[0], point[1] - axis[1], 0.0]) * 0.8 + UP * 0.45)

    def shade_at(point):
        axis = trunk_at(point[2])[0]
        depth = math.hypot(point[0] - axis[0], point[1] - axis[1]) / reach_at(min(max(point[2], base), height - 0.6))
        rel = (point[2] - base) / (height - base)
        return (0.50 + 0.50 * _smoothstep(0.15, 0.9, depth)) * (0.78 + 0.22 * _smoothstep(0.0, 0.8, rel))

    centres = []

    def frond(at, aim, face, length, bough, s):
        """A needle spray from `at` along `aim`, lying in the plane whose normal is nearest `face`."""
        side = _unit(np.cross(aim, face))
        wind_base = sway(at, s)
        tone = _card_tone(rng, shade_at(at + aim * length * 0.3))
        tip = np.minimum(tone * (shade_at(at + aim * length) / max(shade_at(at + aim * length * 0.3), 1e-3)) ** 0.5, 1.0)
        _card(
            geometry, at - aim * length * 0.03, aim, side, length, length * 0.9, rng.randrange(4), rng.random() < 0.5, 1,
            wind_base, (bough.phase + rng.uniform(-0.05, 0.05)) % 1.0, normal_at, tone * 0.88, tip,
        )
        centres.append(at + aim * length * 0.5)

    # Each bough carries fronds two ways: a deck of them lying along it, one
    # overlapping the next out past its tip, and more hanging from it (spruce)
    # or spread to either side (fir). How many is set by what the budget allows.
    def frond_size(bough):
        return min(spec["frond"], max(0.55, bough.length * 0.8))

    budget = (TREE_BUDGET - geometry.triangles) // 2 - 4
    decks = {id(bough): max(1, int(round(bough.length / (frond_size(bough) * 0.8)))) for bough in boughs}
    spare = budget - 2 * sum(decks.values())
    weight = sum(bough.length / frond_size(bough) for bough in boughs)
    for bough in boughs:
        size = frond_size(bough) * rng.uniform(0.9, 1.1)
        number = decks[id(bough)]
        for i in range(number):
            reach = bough.length * (i + 1) / number  # where this frond's tip comes on the bough
            s = max(bough.length * 0.04, reach - size * 0.70)
            at, _, _, _ = bough.at(s / bough.length)
            heading = bough.at(min(1.0, (s + size * 0.4) / bough.length))[1]
            across = _unit(np.cross(heading, UP))
            above = np.cross(across, heading)
            for hand in (-1, 1):  # two wings, sloping away to either side like a roof
                roll = (0.25 + 0.6 * spec["hang"]) * rng.uniform(0.6, 1.15)
                aim = _unit(heading + across * hand * rng.uniform(0.15, 0.45) + UP * rng.uniform(-0.05, 0.12))
                face = _unit(above * math.cos(roll) + across * hand * math.sin(roll))
                frond(at, aim, _unit(face - aim * float(np.dot(face, aim))), size, bough, s)
        hung = max(1, int(spare * (bough.length / frond_size(bough)) / weight))
        hand = rng.choice((-1, 1))
        for j in range(hung):
            s = bough.length * (0.12 + 0.82 * (j + rng.uniform(0.15, 0.85)) / hung)
            at, heading, _, _ = bough.at(s / bough.length)
            across = _unit(np.cross(heading, UP)) * hand
            hang = spec["hang"] * rng.uniform(0.75, 1.2)
            aim = _unit(across * (1.0 - hang) * 0.9 + heading * rng.uniform(0.25, 0.6) - UP * hang + across * rng.uniform(-0.25, 0.25) * hang)
            face = UP * (1.0 - spec["hang"]) + (across + heading * rng.uniform(-0.6, 0.6)) * spec["hang"]
            face = _unit(face - aim * float(np.dot(face, aim)))
            frond(at, aim, face, size * rng.uniform(0.7, 1.0), bough, s)
            hand = -hand
    # The leader: two fronds crossed, pointing straight up.
    crest, _ = trunk_at(top - 0.25)
    lead = _Limb([crest, crest + UP], [0.01, 0.01], 1, rng.random(), 0.0)
    for face in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]):
        frond(crest, UP, np.array(face), height - crest[2], lead, furthest * 0.6)

    name = f"Tree{kind.capitalize()}_{int(seed)}"
    mesh = geometry.to_mesh(name, [bark, needles])

    def lump():
        """The stand-in without a picture: a trunk and a stack of ragged cones."""
        far = _Geometry()
        far_points = [trunk_at(0.0)[0], trunk_at(base + 0.6)[0]]
        far.tube(far_points, [trunk.radii[0] * 0.9, trunk_at(base + 0.6)[1]], 5, [0.0, sway(far_points[1], 0.0)], 0.0, 0, tip=False)
        tiers, around = 5, 8
        white = (1.0, 1.0, 1.0)
        for tier in range(tiers):
            z0 = base + (height - base) * (tier / tiers) ** 1.15 - (0.25 if tier == 0 else 0.0)
            z1 = min(height, base + (height - base) * ((tier + 1.55) / tiers) ** 1.15)
            apex = trunk_at(min(z1, top))[0]
            apex[2] = z1
            twist = rng.uniform(0.0, math.tau)
            ring = []
            for j in range(around):
                angle = twist + math.tau * j / around
                r = reach_at(z0 + 0.3) * rng.uniform(0.72, 1.0)
                ring.append(trunk_at(z0)[0] + np.array([math.cos(angle) * r, math.sin(angle) * r, rng.uniform(-0.25, 0.1)]))
            hub = trunk_at(z0)[0] + UP * 0.35
            phase = rng.random()
            for j in range(around):
                a, b = ring[j], ring[(j + 1) % around]
                wa, wb = (sway(a, furthest * 0.7), phase), (sway(b, furthest * 0.7), phase)
                far.face([a, b, apex], [(0.5, 0.5)] * 3, [wa, wb, (sway(apex, 0.0), phase)], [normal_at(a), normal_at(b), _unit(normal_at((a + b) / 2) + UP)], [white] * 3, 1)
                far.face([b, a, hub], [(0.5, 0.5)] * 3, [wb, wa, (sway(hub, 0.0), phase)], [_unit(normal_at(b) - UP), _unit(normal_at(a) - UP), -UP], [white] * 3, 1)
        materials = [flat_material("FarBark", (0.085, 0.06, 0.04), roughness=0.95), far_material(spec["far"], spec["material"])]
        return far.to_mesh(f"{name}_far", materials, colors=False), far.triangles

    far_mesh, far_triangles = _stand_in(name, mesh, spec["material"], 3, lump)

    centres = np.array(centres)
    plant = Plant(
        mesh=mesh, far=far_mesh, height=float(height), trunk_radius=float(trunk_radius),
        crown_radius=float(np.percentile(np.hypot(centres[:, 0], centres[:, 1]), 95) + 0.4),
        triangles=geometry.triangles, far_triangles=far_triangles, kind=kind, seed=int(seed),
    )
    _cache[key] = plant
    return plant


# --- Bushes and hedges ---------------------------------------------------------------

BUSHES = {
    # A dense rounded shrub, small toothed leaves: hawthorn, privet, blackthorn.
    "round": dict(material="LeavesThorn", far="FarLeavesThorn", height=(1.0, 1.4), width=1.25, card=0.58, stems=(5, 7), dome=1.0, cards=150),
    # A taller, looser one with arching stems and compound leaves: elder, rowan, dog rose.
    "tall": dict(material="LeavesAsh", far="FarLeavesAsh", height=(1.45, 1.8), width=0.85, card=0.80, stems=(4, 6), dome=0.7, cards=130),
    # A low spreading mound, lobed leaves: bramble, currant, young oak scrub.
    "low": dict(material="LeavesOak", far="FarLeavesOak", height=(0.6, 0.85), width=2.1, card=0.62, stems=(5, 7), dome=1.25, cards=140),
}
_BUSH_ORDER = ["round", "tall", "low"]


def _shell(rng, count, radii, base, dome, floor=0.08):
    """Points spread evenly over a dome (a ball of `radii` standing on `base`), with the way out at each."""
    result = []
    turn = rng.uniform(0.0, math.tau)
    for i in range(count):
        h = floor + (1.0 - floor) * (1.0 - (i + 0.5) / count) ** dome  # height on the unit dome, more of them up top
        h = min(h, 0.985)
        angle = turn + i * 2.39996
        r = math.sqrt(1.0 - h * h)
        out = np.array([r * math.cos(angle), r * math.sin(angle), h])
        result.append((base + out * radii, _unit(out / radii)))
    return result


def bush(variant=0, seed=0, bark=None):
    """A shrub, 0.6 to 1.8 m: variant 0 / "round", 1 / "tall", 2 / "low". Leaf cards from the tree atlases, drawn smaller."""
    kind = _variant(variant, _BUSH_ORDER)
    key = ("bush", kind, int(seed))
    plant = _cached(key)
    if plant is not None:
        return plant
    spec = BUSHES[kind]
    rng = random.Random(3000017 * (_BUSH_ORDER.index(kind) + 1) + int(seed) * 7919)
    bark = bark or bark_material()
    leaves = foliage_material(spec["material"])
    metres = float(bark.get("metres", 1.0))

    height = rng.uniform(*spec["height"])
    width = height * spec["width"] * rng.uniform(0.9, 1.1)
    size = spec["card"]
    # The leaf cards stand out beyond the stems, so the woody dome is that much smaller.
    radii = np.array([max(width / 2 - size * 0.42, 0.12), max(width / 2 * rng.uniform(0.85, 1.0) - size * 0.42, 0.12), max(height - size * 0.5, 0.2)])
    base = np.array([0.0, 0.0, 0.03])

    def sway(point):
        return float(min(0.5, 0.5 * (max(point[2], 0.0) / height) ** 1.3))

    geometry = _Geometry()
    stems = []
    turn = rng.uniform(0.0, math.tau)
    number = rng.randint(*spec["stems"])
    for i in range(number):
        angle = turn + math.tau * i / number + rng.uniform(-0.3, 0.3)
        h = rng.uniform(0.45, 0.95)
        r = math.sqrt(1.0 - h * h)
        end = base + np.array([r * math.cos(angle), r * math.sin(angle), h]) * radii * rng.uniform(0.8, 1.0)
        start = np.array([math.cos(angle) * 0.06, math.sin(angle) * 0.06, -0.08])
        outward = np.array([math.cos(angle), math.sin(angle), 0.0])
        path = _curve(rng, start, _unit(UP + outward * 0.35), end, _unit(outward + UP * 0.3), 3, 0.07)
        stem = _Limb(path, _taper(3, 0.016 + height * 0.012, 0.006), 1, rng.random(), 0.0)
        stems.append(stem)
        geometry.tube(stem.points, stem.radii, 3, [sway(p) for p in stem.points], stem.phase, 0, metres=metres, v_start=stem.phase * 3.0)
    ends = np.array([stem.points[-1] for stem in stems])

    centre = base + np.array([0.0, 0.0, radii[2] * 0.25])

    def normal_at(point):
        return _unit(_unit((point - centre) / (radii + size * 0.4)) * 0.75 + UP * 0.4)

    cards = int(min(spec["cards"], (BUSH_BUDGET - geometry.triangles) // 2) / 1.2)  # a fifth of sprays are two cards
    centres = []
    for at, out in _shell(rng, cards, radii, base, spec["dome"]):
        at = base + (at - base) * rng.uniform(0.55, 1.0)
        stem = stems[int(np.argmin(np.linalg.norm(ends - at, axis=1)))]
        aim, sides = _spray(rng, out, _unit(out + UP * 0.6), flat=0.8, upright=0.75)
        length = size * rng.uniform(0.75, 1.15)
        depth = np.linalg.norm((at - base) / radii)
        shade = (0.55 + 0.45 * _smoothstep(0.4, 1.0, depth)) * (0.72 + 0.28 * _smoothstep(0.0, 0.6, at[2] / height))
        tone = _card_tone(rng, shade)
        phase = (stem.phase + rng.uniform(-0.06, 0.06)) % 1.0
        for across in sides:
            _card(
                geometry, at - aim * length * 0.4, aim, across, length, length, rng.randrange(4), rng.random() < 0.5, 1,
                sway(at), phase, normal_at, tone * 0.85, np.minimum(tone * 1.1, 1.0),
            )
        centres.append(at + aim * length * 0.1)
        if geometry.triangles >= BUSH_BUDGET - 3:
            break

    name = f"Bush{kind.capitalize()}_{int(seed)}"
    mesh = geometry.to_mesh(name, [bark, leaves])

    centres = np.array(centres)

    def lump():
        """The stand-in without a picture: one leaf-coloured ball."""
        far = _Geometry()
        low, high = centres.min(axis=0), centres.max(axis=0)
        middle = (low + high) / 2
        middle[2] = high[2] * 0.42
        _add_blob(far, middle, [(high[0] - low[0]) / 2 + size * 0.2, (high[1] - low[1]) / 2 + size * 0.2, high[2] * 0.62 + size * 0.1], 0, (0.5, rng.random()), base, turn=rng.uniform(0, math.tau))
        return far.to_mesh(f"{name}_far", [far_material(spec["far"], spec["material"])], colors=False), far.triangles

    far_mesh, far_triangles = _stand_in(name, mesh, spec["material"], 2, lump)

    top = float(max(v[2] for v in geometry.vertices))
    plant = Plant(
        mesh=mesh, far=far_mesh, height=top, trunk_radius=0.08, crown_radius=float(np.percentile(np.hypot(centres[:, 0], centres[:, 1]), 95) + size * 0.3),
        triangles=geometry.triangles, far_triangles=far_triangles, kind=kind, seed=int(seed),
    )
    _cache[key] = plant
    return plant


def hedge_segment(length=2.0, height=1.4, seed=0, thickness=None, material="LeavesThorn", closed_ends=False):
    """
    A straight run of clipped hedge along X, centred on the origin, standing
    on z = 0. Its ends are left bare so that segments set end to end run
    together; pass `closed_ends=True` for a piece that stands alone or ends a
    run. Inside is a dark opaque core, so the hedge cannot be seen through
    however the cards fall. About 110 triangles per metre at 1.4 m high.
    """
    key = ("hedge", round(float(length), 3), round(float(height), 3), int(seed), thickness, material, closed_ends)
    plant = _cached(key)
    if plant is not None:
        return plant
    rng = random.Random(4000037 + int(seed) * 7919 + int(length * 100) * 31 + int(height * 100))
    leaves = foliage_material(material)
    average = np.asarray(_atlas(material)[0]["average"])
    size = min(0.62, max(0.42, height * 0.42))
    thickness = thickness or min(0.9, max(0.5, height * 0.55))
    half_x, half_y = length / 2, thickness / 2
    skin = 0.15  # how far the leaves stand off the core
    core_y, core_z = max(half_y - skin, 0.08), max(height - skin, 0.2)

    def section(t, grow=0.0):
        """A point on the hedge's cross-section (a square-shouldered arch), t from 0 at one foot over the top to 1 at the other, and the way out there."""
        angle = math.pi * t
        c, s = math.cos(angle), math.sin(angle)
        y = -math.copysign(abs(c) ** 0.5, c) * (core_y + grow)
        z = abs(s) ** 0.5 * (core_z + grow)
        normal = _unit([0.0, -math.copysign(abs(c) ** 1.5, c) / (core_y + grow), abs(s) ** 1.5 / (core_z + grow)])
        return y, z, normal

    # The arch's parameter crowds toward its shoulders; this spreads positions evenly along its length instead.
    table = np.linspace(0.0, 1.0, 241)
    outline = np.array([section(t)[:2] for t in table])
    travelled = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(outline, axis=0), axis=1))])

    def evenly(u):
        return float(np.interp(u * travelled[-1], travelled, table))

    def sway(z):
        return 0.3 * (max(z, 0.0) / height) ** 1.3

    geometry = _Geometry()
    core = flat_material("HedgeCore", tuple(float(c) for c in average * 0.4), roughness=1.0)
    steps = 6
    white = (1.0, 1.0, 1.0)

    def arch(target, grow, slot):
        ring = [section(i / steps, grow) for i in range(steps + 1)]
        for (y0, z0, n0), (y1, z1, n1) in zip(ring[:-1], ring[1:]):
            target.face(
                [(-half_x, y0, z0), (-half_x, y1, z1), (half_x, y1, z1), (half_x, y0, z0)],
                [(0.5, 0.5)] * 4, [(sway(z0), 0.0), (sway(z1), 0.0), (sway(z1), 0.5), (sway(z0), 0.5)], [n0, n1, n1, n0], [white] * 4, slot,
            )
        for x, out in ((-half_x, -1.0), (half_x, 1.0)):  # the ends, as fans
            for (y0, z0, _), (y1, z1, _) in zip(ring[:-1], ring[1:]):
                points = [(x, 0.0, 0.0), (x, y0, z0), (x, y1, z1)]
                target.face(points if out < 0 else points[::-1], [(0.5, 0.5)] * 3, [(sway(p[2]), 0.25 + 0.25 * out) for p in (points if out < 0 else points[::-1])], [(out, 0.0, 0.0)] * 3, [white] * 3, slot)

    arch(geometry, 0.0, 0)

    def normal_at(point):
        return _unit(_unit([0.0, point[1] / half_y, max(point[2] - height * 0.45, 0.0) / (height * 0.55)]) * 0.75 + UP * 0.4)

    def spray(at, out, flat):
        aim, sides = _spray(rng, out, _unit(UP + np.array([rng.uniform(-0.8, 0.8), 0.0, 0.0])), flat=flat, upright=1.0)
        reach = size * rng.uniform(0.8, 1.15)
        tone = _card_tone(rng, 0.66 + 0.34 * _smoothstep(0.0, 0.7, at[2] / height))
        phase = rng.random()
        for across in sides:
            start = at - aim * reach * 0.45 + out * 0.02
            over = max(start[2], start[2] + aim[2] * reach) + abs(across[2]) * reach * 0.5 - height
            if over > 0:
                start = start - UP * over  # clipped: nothing stands above the height asked for
            _card(geometry, start, aim, across, reach, reach, rng.randrange(4), rng.random() < 0.5, 1, sway(at[2]), phase, normal_at, tone * 0.85, np.minimum(tone * 1.1, 1.0))

    perimeter = float(travelled[-1])
    cards = max(10, int(round(length * perimeter * 7.5 / size**2 / 1.2)))  # about two and a half leaves deep; a fifth of sprays are two cards
    for k in range(cards):
        x = -half_x + length * (k + rng.uniform(0.1, 0.9)) / cards
        t = evenly(0.04 + 0.92 * ((k * 0.6180339887 + rng.uniform(-0.04, 0.04)) % 1.0))
        y, z, out = section(t, skin * rng.uniform(0.3, 1.0))
        spray(np.array([x, y, z]), out, 0.8)
    if closed_ends:
        for x, out in ((-half_x, -1.0), (half_x, 1.0)):
            for k in range(max(3, int(round(core_y * 2 * core_z * 4.6 / size**2)))):
                at = np.array([x + out * skin * 0.6, rng.uniform(-core_y, core_y) * 0.8, core_z * rng.uniform(0.12, 0.9)])
                spray(at, np.array([out, 0.0, 0.0]), 1.0)
    name = f"Hedge_{length:g}x{height:g}_{int(seed)}" + ("_ends" if closed_ends else "")
    mesh = geometry.to_mesh(name, [core, leaves])

    far = _Geometry()
    arch(far, skin, 0)
    far_mesh = far.to_mesh(f"{name}_far", [far_material("Far" + material, material)], colors=False)
    plant = Plant(
        mesh=mesh, far=far_mesh, height=float(max(v[2] for v in geometry.vertices)), trunk_radius=float(half_y), crown_radius=float(half_y + skin),
        triangles=geometry.triangles, far_triangles=far.triangles, kind="hedge", seed=int(seed), length=float(length), thickness=float(thickness),
    )
    _cache[key] = plant
    return plant


# --- Scanned plants --------------------------------------------------------------------


def _triangle_count(mesh):
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def _decimated(source, name, triangles):
    """A copy of mesh `source` reduced to about `triangles` (never more)."""
    have = _triangle_count(source)
    holder = bpy.data.objects.new("plants_decimate", source)
    bpy.context.scene.collection.objects.link(holder)
    try:
        ratio = min(1.0, triangles / max(have, 1))
        result = None
        for _ in range(6):  # the modifier overshoots a little on meshes made of many loose parts
            if result is not None:
                bpy.data.meshes.remove(result)
            modifier = holder.modifiers.new("Decimate", "DECIMATE")
            modifier.ratio = ratio
            modifier.use_collapse_triangulate = True
            result = bpy.data.meshes.new_from_object(holder.evaluated_get(bpy.context.evaluated_depsgraph_get()))
            holder.modifiers.remove(modifier)
            got = _triangle_count(result)
            if got <= triangles:
                break
            ratio *= 0.94 * triangles / got
    finally:
        bpy.data.objects.remove(holder)
    result.name = name
    return result


def _islands(mesh):
    """Which connected piece each vertex belongs to (leaves and stems of a scan are separate pieces)."""
    count = len(mesh.vertices)
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    label = np.arange(count)
    for _ in range(64):  # min-label spreading; converges fast on leaf-sized pieces
        low = np.minimum(label[edges[:, 0]], label[edges[:, 1]])
        before = label.copy()
        np.minimum.at(label, edges[:, 0], low)
        np.minimum.at(label, edges[:, 1], low)
        label = label[label]  # jump along the chain
        if np.array_equal(before, label):
            break
    return label


def _add_wind(mesh, height, reach):
    """The `wind` UV map for a mesh that has none: sway by height and distance out, phase by connected piece."""
    while len(mesh.uv_layers) > 1:  # wind has to be the second UV map
        mesh.uv_layers.remove(mesh.uv_layers[-1])
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)
    out = np.hypot(positions[:, 0], positions[:, 1]) / max(reach, 1e-6)
    up = np.clip(positions[:, 2] / max(height, 1e-6), 0.0, 1.0)
    sway = np.clip(0.75 * up**1.3 + 0.25 * np.clip(out, 0.0, 1.0) * up**0.5, 0.0, 1.0)
    label = _islands(mesh)
    phase = np.mod(np.sin(label * 12.9898 + 4.1) * 43758.5453, 1.0)
    if len(np.unique(label)) < 3:  # all one piece: phase by the side of the plant it is on
        phase = np.mod(np.arctan2(positions[:, 1], positions[:, 0]) / math.tau, 1.0)
        phase = np.round(phase * 5.0) / 5.0
    corner = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", corner)
    layer = mesh.uv_layers.new(name="wind", do_init=False)
    layer.data.foreach_set("uv", np.stack([sway[corner], phase[corner]], axis=1).astype(np.float32).ravel())
    mesh.uv_layers.active = mesh.uv_layers[0]


def _scan_material(material):
    """
    Tidies a material the glTF importer made for a scan: colour straight from
    its texture (no vertex colours in between), and no cut-out or blending
    unless the texture really has an alpha channel. Poly Haven's plant scans
    are marked as cut-outs but ship JPEG colour, which has none: left alone
    they would cost an alpha test, or blend and sort wrongly, for nothing.
    """
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        return

    def image_behind(socket, depth=0):
        for link in socket.links:
            if link.from_node.type == "TEX_IMAGE":
                return link.from_node
            if depth < 4:
                for upstream in link.from_node.inputs:
                    found = image_behind(upstream, depth + 1)
                    if found is not None:
                        return found
        return None

    picture = image_behind(shader.inputs["Base Color"])
    if picture is not None:
        for link in list(shader.inputs["Base Color"].links):
            links.remove(link)
        links.new(picture.outputs["Color"], shader.inputs["Base Color"])
    alpha = shader.inputs["Alpha"]
    has_alpha = picture is not None and picture.image is not None and picture.image.depth in (32, 64, 128)
    if not has_alpha:
        for link in list(alpha.links):
            links.remove(link)
        alpha.default_value = 1.0
    elif alpha.links and alpha.links[0].from_node.type == "TEX_IMAGE":  # blended: make it a cut-out
        source = alpha.links[0].from_socket
        links.remove(alpha.links[0])
        below = nodes.new("ShaderNodeMath")
        below.operation = "LESS_THAN"
        below.inputs[1].default_value = ALPHA_CUTOFF
        invert = nodes.new("ShaderNodeMath")
        invert.operation = "SUBTRACT"
        invert.inputs[0].default_value = 1.0
        links.new(source, below.inputs[0])
        links.new(below.outputs["Value"], invert.inputs[1])
        links.new(invert.outputs["Value"], alpha)
    material.use_backface_culling = False  # leaves are single sheets
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"


def model_variants(model_id):
    """How many separate plants the model file holds (Poly Haven lays several out in a row)."""
    import json

    with open(os.path.join(MODELS, model_id, f"{model_id}.gltf")) as handle:
        return sum(1 for node in json.load(handle).get("nodes", []) if "mesh" in node)


SCAN_GAIN = 1.6  # how much brighter than photographed a scanned plant is drawn


def import_model(model_id, max_triangles=None, variant=0, far_triangles=60):
    """
    A scanned plant from maps/models/<id>/<id>.gltf as one clean mesh for
    linked duplicates: transforms applied, Z up, its origin where the scan
    has it (on the ground at the foot of the plant, roots a little below),
    cut down to `max_triangles` if given, with a `wind` UV map added.

    The files hold several plants side by side (`model_variants(id)` says how
    many): `variant` picks one, in name order, and `variant=None` joins them
    all as laid out in the file, centred, as a ready-made patch. They are at
    their real size, which is small (the shrubs are 25 to 40 cm tall); scale
    the objects to taste.

    The material is named `<id>` (`<id>_<n>` if there are several) and shared
    by every variant and every call; its images stay as files on disk. `far`
    is the same plant cut down to `far_triangles`: for ground plants this
    small, `lod_skip` is usually the better choice.
    """
    key = ("model", model_id, max_triangles, variant, far_triangles)
    plant = _cached(key)
    if plant is not None:
        return plant
    path = os.path.join(MODELS, model_id, f"{model_id}.gltf")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"[plants] no model '{model_id}' at {path}")

    before = {kind: set(getattr(bpy.data, kind)) for kind in ("objects", "materials", "meshes", "collections")}
    bpy.ops.import_scene.gltf(filepath=path, import_pack_images=False, merge_vertices=True)
    imported = [o for o in bpy.data.objects if o not in before["objects"]]
    parts = sorted((o for o in imported if o.type == "MESH"), key=lambda o: o.name)
    if not parts:
        raise RuntimeError(f"[plants] '{model_id}' has no meshes")
    bpy.context.view_layer.update()
    count = len(parts)
    if variant is not None:
        parts = [parts[int(variant) % count]]

    # Materials: tidied, then one per colour texture, shared with earlier imports of the same model.
    fresh = [m for m in bpy.data.materials if m not in before["materials"]]
    by_texture, renamed = {}, {}
    for material in sorted(fresh, key=lambda m: m.name):
        _scan_material(material)
        shader = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        link = shader.inputs["Base Color"].links[0] if shader is not None and shader.inputs["Base Color"].links else None
        texture = link.from_node.image.filepath if link is not None and link.from_node.type == "TEX_IMAGE" and link.from_node.image else material.name
        if texture not in by_texture:
            by_texture[texture] = len(by_texture)
        renamed[material] = by_texture[texture]
    names = {index: (model_id if len(by_texture) == 1 else f"{model_id}_{index}") for index in by_texture.values()}
    shared = {}
    for material, index in renamed.items():
        existing = bpy.data.materials.get(names[index])
        if index not in shared:
            if existing is not None and existing not in fresh:
                shared[index] = existing  # from an earlier import
            else:
                material.name = names[index]
                shared[index] = material
        renamed[material] = shared[index]

    for material in shared.values():
        if "wind" not in material:
            material["wind"] = WIND * 0.4  # ground plants are small: the same sway as a tree would fold them flat
        # Photographed leaves are darker and glossier than the painted plants
        # around them; cut down to a few hundred flat facets they come out as
        # black plastic. The game takes the gloss off and lifts the colour.
        if "matte" not in material:
            material["matte"] = True
        if "gain" not in material:
            material["gain"] = SCAN_GAIN

    # Join by hand, baking each part's place into its vertices.
    bm = bmesh.new()
    materials = []
    for part in parts:
        copy = part.data.copy()
        while copy.color_attributes:  # scans' vertex colours are not used; the shared material ignores them
            copy.color_attributes.remove(copy.color_attributes[0])
        matrix = part.matrix_world.copy()
        if variant is not None:
            matrix.translation = (0.0, 0.0, 0.0)  # where it stood in the row is no part of the plant
        copy.transform(matrix)
        if matrix.determinant() < 0:
            copy.flip_normals()
        slots = []
        for slot in part.material_slots:
            material = renamed.get(slot.material, slot.material)
            if material not in materials:
                materials.append(material)
            slots.append(materials.index(material))
        faces_before = len(bm.faces)
        bm.from_mesh(copy)
        bm.faces.ensure_lookup_table()
        for face in bm.faces[faces_before:]:
            face.material_index = slots[face.material_index] if slots else 0
        bpy.data.meshes.remove(copy)
    if variant is None:  # the patch, centred
        xs, ys = [v.co.x for v in bm.verts], [v.co.y for v in bm.verts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        for v in bm.verts:
            v.co.x -= cx
            v.co.y -= cy
    low = min(v.co.z for v in bm.verts)
    high = max(v.co.z for v in bm.verts)
    joined = bpy.data.meshes.new(f"{model_id}_full")
    bm.to_mesh(joined)
    bm.free()
    for material in materials:
        joined.materials.append(material)
    for polygon in joined.polygons:
        polygon.use_smooth = True

    for obj in imported:
        bpy.data.objects.remove(obj)
    for mesh in [m for m in bpy.data.meshes if m not in before["meshes"] and m is not joined and m.users == 0]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m not in before["materials"] and m.users == 0]:
        bpy.data.materials.remove(material)
    for group in [c for c in bpy.data.collections if c not in before["collections"] and not c.objects and not c.children]:
        bpy.data.collections.remove(group)

    full = _triangle_count(joined)
    name = model_id + ("" if variant is None else f"_{int(variant) % count}") + ("" if max_triangles is None else f"_{int(max_triangles)}")
    if max_triangles is not None and full > max_triangles:
        mesh = _decimated(joined, name, max_triangles)
    else:
        mesh = joined.copy()
        mesh.name = name
    far = _decimated(joined, f"{name}_far", far_triangles) if full > far_triangles else joined.copy()
    far.name = f"{name}_far"
    far.use_fake_user = True
    bpy.data.meshes.remove(joined)

    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", positions)
    reach = float(np.percentile(np.hypot(positions[0::3], positions[1::3]), 98))
    for each in (mesh, far):
        _add_wind(each, high, reach)

    plant = Plant(
        mesh=mesh, far=far, height=float(high), trunk_radius=float(min(0.1, reach * 0.15)), crown_radius=reach, sunk=float(max(0.0, -low)),
        triangles=_triangle_count(mesh), far_triangles=_triangle_count(far), source_triangles=full, kind=model_id, seed=0,
        variants=count, materials=[m.name for m in materials],
    )
    _cache[key] = plant
    return plant
