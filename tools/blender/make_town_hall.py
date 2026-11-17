"""
Builds the inside of the town's hall -- a medieval great hall -- and saves it
as a .blend.

    blender -b --factory-startup --python tools/blender/make_town_hall.py -- maps/test_town.hall.blend

(or `npm run map:make-hall`, which also exports it to public/maps/test_town/interiors/hall/:
an interior's files live inside the folder of the level it belongs to).

An interior is a level of its own: one closed room the player arrives in
through a door of the town. It is built the way `make_test_town.py` builds
the town, with the helpers that script uses (lifted into `kit.py`):

- The structure is three objects. `Hall` is everything that can be walked on
  or into and everything that must stop sunlight: floor, walls with real
  thickness, the roof, the dais, the fireplace, the stone benches, the bars
  in the windows. `HallTrim` is what nobody can touch and whose shadow nobody
  would miss: the roof timbers, window surrounds, string course, panelling.
  It has no collision and casts no shadow. `HallGlass` is the panes: pale and
  a little brighter than white, so the windows read as daylight from inside,
  and it casts no shadow either, so the sun comes through the openings and
  lies on the floor.
- Furniture is linked duplicates of a few meshes, which the game draws as
  instances.
- Empties mark what the game's code refers to: where the player arrives
  (`spawn`, `entrance`), the way back out (`exit`), the dais, and the fires.

The room is a closed box. Sky and sun get in through the windows and nowhere
else: wherever two parts meet (wall and roof, wall and floor, wall and wall)
one runs on into the other and never merely touches it, and walls and roof
have an outer skin as well as an inner one.

Blender axes: X runs the length of the hall, the dais at -X and the door at
+X; Z is up. The game's sun stands toward Blender (-0.45, -0.66, 0.6), so it
shines in through the long wall at -Y and the great window behind the dais.
"""

import math
import os
import random
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit  # noqa: E402
from kit import UP, Builder, Frame, Placer, add, clip, collection, fbm, lancet_top, marker, material, pointed_arch, smoothstep, value_noise  # noqa: E402
from surfaces import surface  # noqa: E402

# Where the middle of the hall's floor goes. The exporter puts each object in
# the chunk its centre falls in, and its grid has a line through the origin on
# both axes, whatever the chunk size: a room built round the origin has its
# structure in one chunk file and its furniture listed in that and three small
# ones (which costs nothing in the game: they are all in reach at once, and
# linked duplicates are drawn in one batch however many chunks they span).
# Moved to (L + T + 1, W + T + 1), the whole room falls in the one chunk.
ORIGIN = Vector((0.0, 0.0, 0.0))

L, W = 12.0, 5.5  # half the room's length and width, inside the walls
T = 0.8  # wall thickness
EAVES, RIDGE = 6.0, 10.0  # where the roof's underside meets the long walls, and its height along the middle
SLOPE = (RIDGE - EAVES) / W
PITCH = math.atan(SLOPE)
ROOF_THICK = 0.3  # measured straight down
TRUSSES = (-8.0, -4.0, 0.0, 4.0, 8.0)
BAYS = (-10.0, -6.0, -2.0, 2.0, 6.0, 10.0)  # a window in the middle of each
DAIS_FRONT, DAIS = -8.0, 0.3
HEARTH = 2.0  # the fireplace takes this bay of the north wall
ARCH_STEPS = 8  # segments in each side of a pointed arch
GLASS_IN = 0.36  # how far in from the wall's outer face the glass stands: near the middle of the wall
GLASS_GLOW = 1.8  # the panes' brightness; above about 1.7 the game's bloom begins to pick them up

# Windows: (width, sill, where the arch springs) at the glass, at the room's
# face and at the outer face. The glass is the narrowest: the jambs splay both
# ways from it and the sill slopes down into the room. The sun is never square
# to a wall, and through a straight-sided hole in a wall this thick a third of
# it would be lost on the stone. Sills are low for the same reason: the eaves
# shade the head of a tall window, so what light there is comes in lower down.
LANCET = ((1.6, 1.95, 3.7), (2.2, 1.65, 3.4), (2.0, 1.95, 3.5))  # along both long walls
GREAT = ((3.0, 3.6, 5.6), (3.7, 3.2, 5.4), (3.4, 3.6, 5.45))  # behind the dais, of three lights
FLANKING = ((1.0, 3.55, 5.25), (1.5, 3.2, 5.05), (1.4, 3.55, 5.1))  # either side of it
FLANK_AT = 3.6
GABLE = ((1.0, 5.9, 7.0), (1.5, 5.6, 6.85), (1.4, 5.9, 6.9))  # high in the wall over the door
DOOR = (2.6, 0.0, 2.4)  # the entrance: a recess in the east wall, closed by the door
DOOR_IN = 0.45

rng = random.Random(7)


def under(y):
    """Height of the roof's underside, y metres from the middle of the hall."""
    return RIDGE - SLOPE * abs(y)


# --- Surfaces ----------------------------------------------------------------
# The town's own texture sets, so nothing new is downloaded: rubble for the
# walling, cut stone for what a mason would have cut, the plaster set as pale
# dressed stone, and one set of planks for every piece of wood.

kit.reset()

wall_stone = surface("HallWallStone", "defense_wall_02", vertex_colors="Col", even=0.8) or material("HallWallStone", (0.42, 0.37, 0.29), roughness=0.92, vertex_colors=True)
wall_stone["variation"] = 0.6
ashlar = surface("Masonry", "medieval_blocks_03") or material("Masonry", (0.36, 0.33, 0.28), roughness=0.88)
dressed = surface("DressedStone", "white_plaster_02", tint=(0.80, 0.76, 0.66)) or material("DressedStone", (0.55, 0.52, 0.45), roughness=0.95)
FLAG_SIZE = 3.2  # the cut-stone set laid larger, as flagstones
flags = surface("Flagstones", "medieval_blocks_03", tint=(0.86, 0.86, 0.9)) or material("Flagstones", (0.31, 0.30, 0.29), roughness=0.85)
flags["metres"] = FLAG_SIZE
sooty = surface("SootyStone", "medieval_blocks_03", tint=(0.22, 0.20, 0.19)) or material("SootyStone", (0.06, 0.055, 0.05), roughness=0.95)
wood = surface("Wood", "weathered_brown_planks") or material("Wood", (0.19, 0.11, 0.055), roughness=0.85)
dark_wood = surface("DarkWood", "weathered_brown_planks", tint=(0.34, 0.29, 0.26)) or material("DarkWood", (0.07, 0.045, 0.03), roughness=0.8)
iron = material("Iron", (0.05, 0.05, 0.055), roughness=0.5, metallic=0.9)


def leaded_glass(name, quarries=5, metres=0.8, size=256):
    """
    Window glass seen against the day: small diamond panes in a lattice of
    lead, no two quite the same colour, and lit from behind (the picture is
    the material's colour and its glow both). The picture tiles; `metres` is
    how much window one repeat covers, `quarries` how many panes cross it.
    """
    chance = np.random.default_rng(5)
    y, x = np.mgrid[0:size, 0:size] / size
    u, v = (x + y) * quarries, (x - y) * quarries
    lead = np.minimum(np.abs(u - np.round(u)), np.abs(v - np.round(v)))  # how far to the nearest line of lead, in panes
    clear = smoothstep(0.055, 0.095, lead)
    # A tint per pane, the same again one repeat along either axis.
    table = chance.random((2 * quarries, 2 * quarries, 2))
    table = (table + np.roll(table, (quarries, quarries), axis=(0, 1))) / 2
    pane = table[np.floor(u).astype(int) % (2 * quarries), np.floor(v).astype(int) % (2 * quarries)]
    cool, warm = np.array([0.74, 0.86, 1.0]), np.array([0.98, 0.93, 0.74])
    colour = cool + (warm - cool) * smoothstep(0.45, 0.8, pane[..., 0])[..., None]
    colour = colour * (0.72 + 0.28 * pane[..., 1])[..., None]
    colour = colour * (0.9 + 0.1 * smoothstep(0.05, 0.3, lead))[..., None]  # a little darker toward the lead
    rgb = np.array([0.035, 0.035, 0.04]) + (colour - np.array([0.035, 0.035, 0.04])) * clear[..., None]
    stored = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)  # display values, for an 8-bit image
    image = bpy.data.images.new(name, size, size, alpha=False)
    image.pixels.foreach_set(np.concatenate([stored, np.ones((size, size, 1))], axis=2).astype(np.float32).ravel())
    image.pack()

    result = material(name, (0.8, 0.88, 1.0), roughness=0.3, texture=image, glow=GLASS_GLOW)
    nodes, links = result.node_tree.nodes, result.node_tree.links
    shader = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    picture = next(n for n in nodes if n.type == "TEX_IMAGE")
    links.new(picture.outputs["Color"], shader.inputs["Emission Color"])
    result["metres"] = metres
    result["variation"] = 0.0  # the game's light and dark patches are for walls, not for windows
    return result


glass = leaded_glass("HallGlass")
flame = material("CandleFlame", (1.0, 0.62, 0.25), roughness=0.3, glow=7.0)
embers = material("Embers", (1.0, 0.33, 0.07), roughness=0.8, glow=3.0)
wax = material("Wax", (0.80, 0.74, 0.58), roughness=0.6)
pewter = material("Pewter", (0.42, 0.42, 0.44), roughness=0.42, metallic=0.85)
gold = material("Gold", (0.83, 0.58, 0.18), roughness=0.38, metallic=1.0)
clay = material("Clay", (0.40, 0.19, 0.11), roughness=0.7)
bread = material("Bread", (0.52, 0.34, 0.15), roughness=0.9)
cloth_red = material("ClothRed", (0.42, 0.045, 0.04), roughness=0.92)
cloth_blue = material("ClothBlue", (0.045, 0.11, 0.33), roughness=0.92)
cloth_gold = material("ClothGold", (0.62, 0.43, 0.09), roughness=0.85)
linen = material("Linen", (0.74, 0.69, 0.56), roughness=0.95)
if "metres" in dressed:
    dressed["detile"] = 1.0  # no courses to keep straight: let the game shuffle its repeats

# The four walls, each seen from inside the room: `along` runs left to right
# for someone facing the wall, and "out" is into the room.
south = Frame((L, -W), (-1, 0))  # the sunny long wall; along = L - x
north = Frame((-L, W), (1, 0))  # along = x + L
west = Frame((-L, -W), (0, 1))  # behind the dais; along = y + W
east = Frame((L, W), (0, -1))  # the entrance; along = W - y

hall = Builder([wall_stone, ashlar])
trim = Builder([dressed, dark_wood])
panes = Builder([glass])


# --- Walls with pointed openings ------------------------------------------------


def skin(b, F, a0, a1, z0, top, mat, holes=(), d=0.0, flip=False, step=0.0, levels=(), cuts=()):
    """
    One face of a wall from a0 to a1 along the frame, at depth `d`, from z0 up
    to `top` (a height, or a function of `along` for a gable, which must be
    straight between `cuts`). `holes` are pointed openings (centre, width,
    foot, spring). The face is laid as a grid cut along every opening's edges,
    as `Builder.wall` does, so neighbours share whole edges; `step` and
    `levels` add more lines to it, which gives vertex colours somewhere to
    vary. Returns the grid's (columns, rows), for whatever lines the openings.
    """
    top_at = top if callable(top) else (lambda a: top)
    facing = -F.N if flip else F.N
    must = {a0, a1, *cuts}
    for centre, wide, _foot, _spring in holes:
        must |= {centre - wide / 2, centre, centre + wide / 2}
    columns = set(must)
    if step:
        number = max(1, round((a1 - a0) / step))
        for k in range(1, number):
            a = a0 + (a1 - a0) * k / number
            in_hole = any(centre - wide / 2 < a < centre + wide / 2 for centre, wide, _f, _s in holes)
            if not in_hole and all(abs(a - m) > 0.25 for m in must):
                columns.add(a)
    columns = sorted(columns)
    highest = max(top_at(a) for a in columns)
    must = {z0, highest}
    for _centre, wide, foot, spring in holes:
        must |= {foot, spring, lancet_top(wide, spring)}
    rows = set(must)
    for z in levels:
        in_arch = any(spring < z < lancet_top(wide, spring) for _c, wide, _f, spring in holes)
        if z0 < z < highest and not in_arch and all(abs(z - m) > 0.15 for m in must):
            rows.add(z)
    rows = sorted(rows)

    for left, right in zip(columns[:-1], columns[1:]):
        top_left, top_right = top_at(left), top_at(right)
        for bottom, upper in zip(rows[:-1], rows[1:]):
            if bottom >= max(top_left, top_right) - 1e-9:
                continue
            ca, cz = (left + right) / 2, (bottom + upper) / 2
            if any(c - w / 2 < ca < c + w / 2 and foot < cz < lancet_top(w, spring) for c, w, foot, spring in holes):
                continue
            cell = [(left, bottom), (right, bottom), (right, upper), (left, upper)]
            if upper > min(top_left, top_right) + 1e-9:  # under a gable: keep what is below its line
                nx, ny = -(top_right - top_left), right - left
                cell = clip(cell, nx, ny, nx * left + ny * top_left)
            if len(cell) >= 3:
                b.poly([F.at(a, d, z) for a, z in cell], mat, facing)

    # The wall's corners above each arch: one piece each, with a point
    # wherever a line of the grid arrives at it (another opening's, say).
    for centre, wide, _foot, spring in holes:
        left, right = centre - wide / 2, centre + wide / 2
        arch = pointed_arch(left, right, spring, ARCH_STEPS)
        half = len(arch) // 2
        apex = arch[half][1]
        beside = [z for z in rows if spring + 1e-6 < z < apex - 1e-6]
        over_left = [(a, apex) for a in reversed(columns) if left + 1e-6 < a < centre - 1e-6]
        over_right = [(a, apex) for a in reversed(columns) if centre + 1e-6 < a < right - 1e-6]
        corner = [(left, apex)] + [(left, z) for z in reversed(beside)] + arch[: half + 1] + over_left
        b.poly([F.at(a, d, z) for a, z in corner], mat, facing)
        corner = [(right, apex)] + over_right + arch[half:] + [(right, z) for z in beside]
        b.poly([F.at(a, d, z) for a, z in corner], mat, facing)
    return columns, rows


def outline(centre, wide, foot, spring, jamb=(), sill=()):
    """
    A pointed opening's edge: up the left jamb, over the arch, down the right
    jamb and back along the sill. `jamb` and `sill` are places along those
    (0 to 1) to put a point, where the wall beside the opening has an edge.
    """
    left, right = centre - wide / 2, centre + wide / 2
    up = [foot + t * (spring - foot) for t in jamb]
    return (
        [(left, foot)]
        + [(left, z) for z in up]
        + pointed_arch(left, right, spring, ARCH_STEPS)
        + [(right, z) for z in reversed(up)]
        + [(right, foot)]
        + [(left + t * wide, foot) for t in reversed(sill)]
    )


def reveal(b, F, near, far, d_near, d_far, mat, floor=None):
    """The lining of an opening between two outlines at two depths. Leaves out the sill if it lies on the `floor`."""
    middle = Vector(F.at(sum(p[0] for p in near) / len(near), (d_near + d_far) / 2, sum(p[1] for p in near) / len(near)))
    for i in range(len(near)):
        j = (i + 1) % len(near)
        if floor is not None and near[i][1] <= floor + 1e-6 and near[j][1] <= floor + 1e-6:
            continue
        quad = [F.at(near[i][0], d_near, near[i][1]), F.at(near[j][0], d_near, near[j][1]), F.at(far[j][0], d_far, far[j][1]), F.at(far[i][0], d_far, far[i][1])]
        b.poly(quad, mat, middle - sum((Vector(p) for p in quad), Vector()) / 4)


def surround(F, centre, wide, foot, spring, band=0.16, proud=0.05, sill=True, tooth=0.12):
    """
    The cut stone round an opening on the room's face: a ring of arch stones,
    every other one a little prouder, a narrow moulding over them that ends
    in a block at each springing, jamb stones long and short by turns, and a
    sill.
    """
    left, right = centre - wide / 2, centre + wide / 2
    arch = pointed_arch(left, right, spring, ARCH_STEPS)
    half = len(arch) // 2

    def struck(extra):
        """The same arch struck that much wider."""
        scale = (wide + extra) / wide
        points = []
        for k, (a, z) in enumerate(arch):
            if k == half:
                points.append((centre, spring + math.sqrt((wide + extra) ** 2 - (wide / 2) ** 2)))
            else:
                cx = right if k < half else left
                points.append((cx + (a - cx) * scale, spring + (z - spring) * scale))
        return points

    ring, label = struck(band), struck(band + 0.07)
    for k in range(len(arch) - 1):
        trim.fprism(F, [arch[k], arch[k + 1], ring[k + 1], ring[k]], 0.0, proud + (0.014 if k % 2 else 0.0), dressed)
        trim.fprism(F, [ring[k], ring[k + 1], label[k + 1], label[k]], 0.0, proud + 0.06, dressed, sides=[True, False, True, False])
    for side in (-1, 1):
        a0, a1 = sorted((centre + side * (wide / 2 + band - 0.02), centre + side * (wide / 2 + band + 0.11)))
        trim.fbox(F, a0, a1, 0.0, proud + 0.085, spring - 0.13, spring + 0.02, dressed)
    keystone = [(centre - 0.11, arch[half][1] - 0.05), (centre + 0.11, arch[half][1] - 0.05), (centre + 0.15, ring[half][1] + 0.07), (centre - 0.15, ring[half][1] + 0.07)]
    trim.fprism(F, keystone, 0.0, proud + 0.035, dressed)
    courses = max(1, round((spring - foot) / 0.34))
    tall = (spring - foot) / courses
    for i in range(courses):
        reach = band + (tooth if i % 2 == 0 else 0.0)
        z0, z1 = foot + i * tall + 0.007, foot + (i + 1) * tall - 0.007
        trim.fbox(F, left - reach, left, 0.0, proud, z0, z1, dressed)
        trim.fbox(F, right, right + reach, 0.0, proud, z0, z1, dressed)
    if sill:
        trim.fbox(F, left - band - 0.2, right + band + 0.2, 0.0, 0.11, foot - 0.09, foot, dressed)


def window(F, centre, sizes, grid, lights=1):
    """
    A window through the wall whose inner face was laid with `grid`: splayed
    reveals from the room in to the glass, the glass, the reveals beyond it
    out to the wall's outer face, stone bars in front of the glass (which are
    in `Hall`, so their shadow falls across the floor with the window's), and
    the surround.
    """
    (wide, foot, spring), (room_wide, room_foot, room_spring), outside = sizes
    columns, rows = grid
    room_left = centre - room_wide / 2
    jamb = [(z - room_foot) / (room_spring - room_foot) for z in rows if room_foot + 1e-6 < z < room_spring - 1e-6]
    sill = [(a - room_left) / room_wide for a in columns if room_left + 1e-6 < a < room_left + room_wide - 1e-6]
    at_room, at_glass, at_outside = (outline(centre, *size, jamb, sill) for size in ((room_wide, room_foot, room_spring), (wide, foot, spring), outside))
    back = -T + GLASS_IN
    reveal(hall, F, at_room, at_glass, 0.0, back, ashlar)
    reveal(hall, F, at_glass, at_outside, back, -T, ashlar)
    panes.poly([F.at(a, back, z) for a, z in at_glass], glass, F.N)
    surround(F, centre, room_wide, room_foot, room_spring)

    left, right = centre - wide / 2, centre + wide / 2

    def head(a):
        """Height of the arch above a point of the sill."""
        return spring + math.sqrt(max(wide**2 - (a - (right if a < centre else left)) ** 2, 0.0))

    def bar(points, thick=0.055, deep=0.14):
        for (a0, z0), (a1, z1) in zip(points[:-1], points[1:]):
            hall.fprism(F, [(a0 - thick, z0), (a0 + thick, z0), (a1 + thick, z1), (a1 - thick, z1)], back, back + deep, dressed)

    if lights == 1:  # one mullion, parting in a Y to meet the arch
        bar([(centre, foot), (centre, spring + 0.04)])
        for side in (-1, 1):
            a = centre + side * wide * 0.27
            bar([(centre, spring), (a, head(a))])
        saddle_bars = [foot + (spring - foot) * t for t in (0.33, 0.67)]
    else:
        # Mullions, each carried on into the head as two arcs of the arch's own
        # radius, which cross: the simplest tracery there is.
        mullions = [left + wide * k / lights for k in range(1, lights)]
        for m in mullions:
            bar([(m, foot), (m, spring + 0.04)], thick=0.07, deep=0.2)
            for side in (-1, 1):
                cx = m + side * wide
                stop = (cx + (left if side > 0 else right)) / 2  # where it meets the arch
                points = []
                for k in range(7):
                    a = m + (stop - m) * k / 6
                    points.append((a, spring + math.sqrt(max(wide**2 - (a - cx) ** 2, 0.0))))
                bar(points, thick=0.06, deep=0.18)
        saddle_bars = [foot + (spring - foot) * t for t in (0.25, 0.5, 0.75)]
        hall.fbox(F, left, right, back, back + 0.16, (foot + spring) / 2 - 0.06, (foot + spring) / 2 + 0.06, dressed)  # a transom
    for z in saddle_bars:  # the iron bars the glazing is tied to
        hall.fbox(F, left, right, back, back + 0.05, z - 0.014, z + 0.014, iron)


def window_hole(sizes, centre):
    """The holes a window needs in the wall's outer face and in its inner one."""
    _glass, room, outside = sizes
    return (centre, *outside), (centre, *room)


# --- The shell: floor, walls, roof ---------------------------------------------

# The floor, in metre squares so that wear and dirt can be painted on it (see
# "Weathering"), running on under the walls to their outer faces.
floor_x = [-L - T] + [-L + k for k in range(int(2 * L) + 1)] + [L + T]
floor_y = [-W - T] + [-W + k for k in range(int(2 * W) + 1)] + [W + T]
for x0, x1 in zip(floor_x[:-1], floor_x[1:]):
    for y0, y1 in zip(floor_y[:-1], floor_y[1:]):
        hall.poly([(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)], flags, UP, tile=FLAG_SIZE)

LEVELS = (0.6, 1.2, 2.5, 4.4)
# The long walls run a hand's width on into the end walls, and every wall a
# little way up into the roof and down through the floor.
for F, places in ((south, [L - x for x in BAYS]), (north, [x + L for x in BAYS if x != HEARTH])):
    holes = [window_hole(LANCET, a) for a in places]
    grid = skin(hall, F, -0.05, 2 * L + 0.05, -0.3, EAVES + 0.05, wall_stone, [inner for _outer, inner in holes], step=1.0, levels=LEVELS)
    skin(hall, F, -T, 2 * L + T, -0.3, under(W + T) + 0.1, ashlar, [outer for outer, _inner in holes], d=-T, flip=True)
    for a in places:
        window(F, a, LANCET, grid)


def gable(a):
    return under(a - W)


west_windows = [(W, GREAT, 3), (W - FLANK_AT, FLANKING, 1), (W + FLANK_AT, FLANKING, 1)]
holes = [window_hole(sizes, a) for a, sizes, _lights in west_windows]
grid = skin(hall, west, 0.0, 2 * W, -0.3, lambda a: gable(a) + 0.05, wall_stone, [inner for _outer, inner in holes], step=1.0, levels=(0.9, 2.2, 4.3, 9.3))
skin(hall, west, -T, 2 * W + T, -0.3, lambda a: gable(a) + 0.1, ashlar, [outer for outer, _inner in holes], d=-T, flip=True)
for a, sizes, lights in west_windows:
    window(west, a, sizes, grid, lights=lights)

gable_outer, gable_inner = window_hole(GABLE, W)
door_hole = (W, *DOOR)
grid = skin(hall, east, 0.0, 2 * W, -0.3, lambda a: gable(a) + 0.05, wall_stone, [door_hole, gable_inner], step=1.0, levels=(1.2, 9.3))
skin(hall, east, -T, 2 * W + T, -0.3, lambda a: gable(a) + 0.1, ashlar, [gable_outer], d=-T, flip=True)
window(east, W, GABLE, grid)

# The roof: a slab on each side, boarded underneath, running on past the walls.
boards = wood
for side in (1, -1):
    x0, x1 = -L - T - 0.2, L + T + 0.2
    edge = side * (W + T + 0.12)
    low = under(edge)
    hall.poly([(x0, 0, RIDGE), (x1, 0, RIDGE), (x1, edge, low), (x0, edge, low)], boards, -UP, grain=(1, 0, 0))
    hall.poly([(x0, 0, RIDGE + ROOF_THICK), (x1, 0, RIDGE + ROOF_THICK), (x1, edge, low + ROOF_THICK), (x0, edge, low + ROOF_THICK)], ashlar, UP)
    hall.poly([(x0, edge, low), (x1, edge, low), (x1, edge, low + ROOF_THICK), (x0, edge, low + ROOF_THICK)], ashlar, (0, side, 0))
    for x, out in ((x0, -1), (x1, 1)):
        hall.poly([(x, 0, RIDGE), (x, edge, low), (x, edge, low + ROOF_THICK), (x, 0, RIDGE + ROOF_THICK)], ashlar, (out, 0, 0))


# --- The entrance ----------------------------------------------------------------
# A double door in a pointed arch, closed: a recess in the wall with the door
# at its back. (The game's `exit` marker is what takes the player out.)

columns, rows = grid
jamb = [(z - DOOR[1]) / (DOOR[2] - DOOR[1]) for z in rows if DOOR[1] + 1e-6 < z < DOOR[2] - 1e-6]
across = [(a - (W - DOOR[0] / 2)) / DOOR[0] for a in columns if W - DOOR[0] / 2 + 1e-6 < a < W + DOOR[0] / 2 - 1e-6]
door_edge = outline(W, *DOOR, jamb, across)
reveal(hall, east, door_edge, door_edge, 0.0, -DOOR_IN, ashlar, floor=0.0)
hall.poly([east.at(a, -DOOR_IN, z) for a, z in door_edge], dark_wood, east.N, grain=UP)
hall.fbox(east, W - DOOR[0] / 2, W + DOOR[0] / 2, -DOOR_IN, 0.3, -0.1, 0.04, ashlar)  # the threshold
surround(east, W, *DOOR, band=0.3, proud=0.07, sill=False)
door_back = east.shifted(0.0, -DOOR_IN)
door_top = lancet_top(DOOR[0], DOOR[2])
trim.fbox(door_back, W - 0.07, W + 0.07, 0.0, 0.07, 0.04, door_top - 0.02, dark_wood)  # where the two leaves meet
for side in (-1, 1):
    for z in (0.45, 1.35, 2.25):  # strap hinges, each ending in a point, with a row of studs
        a0, a1 = sorted((W + side * 1.28, W + side * 0.42))
        trim.fbox(door_back, a0, a1, 0.0, 0.025, z, z + 0.1, iron)
        tip = W + side * 0.26
        trim.fprism(door_back, [(W + side * 0.42, z), (W + side * 0.42, z + 0.1), (tip, z + 0.05)], 0.0, 0.025, iron)
        for k in range(4):
            stud = W + side * (1.15 - 0.2 * k)
            trim.fbox(door_back, stud - 0.022, stud + 0.022, 0.0, 0.045, z + 0.028, z + 0.072, iron)
    # A ring to pull it by, on a plate.
    plate = W + side * 0.26
    trim.fbox(door_back, plate - 0.07, plate + 0.07, 0.0, 0.02, 1.02, 1.22, iron)
    for k in range(8):
        p, q = math.tau * k / 8, math.tau * (k + 1) / 8
        trim.rod(door_back.at(plate + 0.085 * math.sin(p), 0.045, 1.07 - 0.085 * math.cos(p)), door_back.at(plate + 0.085 * math.sin(q), 0.045, 1.07 - 0.085 * math.cos(q)), 0.012, iron, segments=4)
# The door's boards: a rail across the foot and at the springing, and a board down each edge.
trim.fbox(door_back, W - DOOR[0] / 2, W + DOOR[0] / 2, 0.0, 0.035, 0.04, 0.3, dark_wood)
for side in (-1, 1):
    a0, a1 = sorted((W + side * DOOR[0] / 2, W + side * (DOOR[0] / 2 - 0.12)))
    trim.fbox(door_back, a0, a1, 0.0, 0.03, 0.3, DOOR[2], dark_wood)


# --- The dais ---------------------------------------------------------------------
# A boarded platform across the west end, two low steps up.

hall.box((-L, -W, -0.1), (DAIS_FRONT, W, DAIS), wood, grain=(0, 1, 0))
hall.box((DAIS_FRONT, -W, -0.1), (DAIS_FRONT + 0.4, W, DAIS / 2), wood, grain=(0, 1, 0))
for x, z in ((DAIS_FRONT, DAIS), (DAIS_FRONT + 0.4, DAIS / 2)):  # a darker nosing on each step
    trim.box((x - 0.06, -W, z - 0.05), (x + 0.025, W, z + 0.012), dark_wood, grain=(0, 1, 0))


# --- The fireplace ------------------------------------------------------------------
# On the north wall: a breast a metre proud of it, a deep firebox under a great
# stone lintel on corbels, and a hood that narrows to a flue and goes out
# through the roof.

HEARTH_A = HEARTH + L
FIRE_HALF, BREAST_HALF, BREAST = 1.05, 1.8, 1.0
LINTEL_FOOT, LINTEL_TOP = 1.7, 2.18
HOOD_TOP, FLUE_HALF, FLUE = 4.7, 0.9, 0.55
for side in (-1, 1):
    a0, a1 = sorted((HEARTH_A + side * BREAST_HALF, HEARTH_A + side * FIRE_HALF))
    hall.fbox(north, a0, a1, 0.0, BREAST, -0.1, LINTEL_FOOT, ashlar)
hall.fbox(north, HEARTH_A - FIRE_HALF, HEARTH_A + FIRE_HALF, 0.0, 0.14, -0.1, LINTEL_FOOT, sooty)  # the fireback
for side in (-1, 1):  # the firebox's cheeks, blackened
    a = HEARTH_A + side * (FIRE_HALF - 0.004)
    hall.poly([north.at(a, 0.14, 0.0), north.at(a, BREAST - 0.12, 0.0), north.at(a, BREAST - 0.12, LINTEL_FOOT), north.at(a, 0.14, LINTEL_FOOT)], sooty, north.U * -side)
hall.fbox(north, HEARTH_A - BREAST_HALF - 0.12, HEARTH_A + BREAST_HALF + 0.12, 0.0, BREAST + 0.08, LINTEL_FOOT, LINTEL_TOP, dressed)
hall.fbox(north, HEARTH_A - BREAST_HALF - 0.1, HEARTH_A + BREAST_HALF + 0.1, 0.0, BREAST + 0.65, -0.1, 0.1, ashlar)  # the hearthstone
# The hood: from the lintel's full size in to the flue.
low = [(HEARTH_A - BREAST_HALF, BREAST), (HEARTH_A + BREAST_HALF, BREAST)]
high = [(HEARTH_A - FLUE_HALF, FLUE), (HEARTH_A + FLUE_HALF, FLUE)]
z0, z1 = LINTEL_TOP, HOOD_TOP
HOOD_BANDS = 5
for k in range(HOOD_BANDS):  # in courses, so the slope has edges to catch the light
    t0, t1 = k / HOOD_BANDS, (k + 1) / HOOD_BANDS

    def at(corner, t):
        a = low[corner][0] + (high[corner][0] - low[corner][0]) * t
        d = low[corner][1] + (high[corner][1] - low[corner][1]) * t
        return a, d, z0 + (z1 - z0) * t

    (la0, d0, h0), (ra0, _, _), (la1, d1, h1), (ra1, _, _) = at(0, t0), at(1, t0), at(0, t1), at(1, t1)
    hall.poly([north.at(la0, d0, h0), north.at(ra0, d0, h0), north.at(ra1, d1, h1), north.at(la1, d1, h1)], ashlar, north.N + UP * 0.3)
    hall.poly([north.at(la0, 0.0, h0), north.at(la0, d0, h0), north.at(la1, d1, h1), north.at(la1, 0.0, h1)], ashlar, -north.U)
    hall.poly([north.at(ra0, 0.0, h0), north.at(ra0, d0, h0), north.at(ra1, d1, h1), north.at(ra1, 0.0, h1)], ashlar, north.U)
    if 0 < k:  # a thin proud course between them
        trim.fprism(north, [(la0 - 0.03, h0 - 0.04), (ra0 + 0.03, h0 - 0.04), (ra0 + 0.03, h0 + 0.04), (la0 - 0.03, h0 + 0.04)], 0.0, d0 + 0.03, dressed)
hall.fbox(north, HEARTH_A - FLUE_HALF, HEARTH_A + FLUE_HALF, 0.0, FLUE, HOOD_TOP, under(W - FLUE) + 0.6, ashlar)
trim.fbox(north, HEARTH_A - FLUE_HALF - 0.05, HEARTH_A + FLUE_HALF + 0.05, 0.0, FLUE + 0.05, HOOD_TOP - 0.06, HOOD_TOP + 0.08, dressed)
# The mantel: a shelf on the lintel, and a corbel under each of its ends.
trim.fbox(north, HEARTH_A - BREAST_HALF - 0.22, HEARTH_A + BREAST_HALF + 0.22, 0.0, BREAST + 0.2, LINTEL_TOP, LINTEL_TOP + 0.1, dressed)
trim.fbox(north, HEARTH_A - BREAST_HALF - 0.16, HEARTH_A + BREAST_HALF + 0.16, 0.0, BREAST + 0.13, LINTEL_TOP - 0.07, LINTEL_TOP, dressed)
for side in (-1, 1):
    a = HEARTH_A + side * (FIRE_HALF + 0.14)
    profile = [(BREAST, LINTEL_FOOT - 0.5), (BREAST + 0.1, LINTEL_FOOT - 0.36), (BREAST + 0.1, LINTEL_FOOT), (BREAST, LINTEL_FOOT)]
    trim.prism([north.at(a - 0.14, d, z) for d, z in profile], north.U * 0.28, dressed, back=True)
    for k in range(3):  # jamb stones standing a little proud, as at the windows
        z = 0.12 + k * 0.42
        a0, a1 = sorted((HEARTH_A + side * BREAST_HALF, HEARTH_A + side * (BREAST_HALF - (0.5 if k % 2 == 0 else 0.3))))
        trim.fbox(north, a0, a1, BREAST, BREAST + 0.035, z, z + 0.4, dressed)
# Andirons and a fire laid on them.
for side in (-1, 1):
    a = HEARTH_A + side * 0.42
    trim.fbox(north, a - 0.02, a + 0.02, 0.3, 0.9, 0.1, 0.13, iron, back=True)
    trim.fbox(north, a - 0.025, a + 0.025, 0.86, 0.91, 0.1, 0.62, iron, back=True)
    trim.fbox(north, a - 0.05, a + 0.05, 0.835, 0.935, 0.62, 0.7, iron, back=True)
    trim.fbox(north, a - 0.02, a + 0.02, 0.3, 0.34, 0.1, 0.24, iron, back=True)
for a0, a1, d, z, radius in ((-0.75, 0.7, 0.42, 0.33, 0.085), (-0.62, 0.8, 0.62, 0.34, 0.1), (-0.7, 0.55, 0.52, 0.5, 0.075), (-0.3, 0.72, 0.36, 0.2, 0.06)):
    trim.rod(north.at(HEARTH_A + a0, d - 0.04, z), north.at(HEARTH_A + a1, d + 0.05, z + 0.03), radius, dark_wood, segments=7)
trim.fbox(north, HEARTH_A - 0.7, HEARTH_A + 0.7, 0.25, 0.75, 0.1, 0.125, embers, back=True)  # a bed of embers
# A pot hung over it from a bar across the firebox.
pot = north.at(HEARTH_A + 0.1, 0.52, 0.0)
trim.rod(north.at(HEARTH_A - FIRE_HALF, 0.52, 1.5), north.at(HEARTH_A + FIRE_HALF, 0.52, 1.5), 0.02, iron, segments=5, caps=False)
trim.rod((pot.x, pot.y, 1.5), (pot.x, pot.y, 1.16), 0.012, iron, segments=4)
for z0, z1, r0, r1 in ((0.72, 0.8, 0.13, 0.2), (0.8, 0.95, 0.2, 0.23), (0.95, 1.04, 0.23, 0.19), (1.04, 1.06, 0.21, 0.21)):
    trim.cylinder((pot.x, pot.y), r0, z0, z1, iron, segments=10, top_radius=r1)
for k in range(6):  # its handle
    p, q = math.pi * k / 6, math.pi * (k + 1) / 6
    trim.rod((pot.x + 0.2 * math.cos(p), pot.y, 1.05 + 0.11 * math.sin(p)), (pot.x + 0.2 * math.cos(q), pot.y, 1.05 + 0.11 * math.sin(q)), 0.009, iron, segments=4)


# --- Stone benches -------------------------------------------------------------------
# Along the long walls below the windows, as great halls had: it is also what
# the wall stands on, where a plain wall would just meet the floor.

BENCHES = ((south, L - 12.0, L - (DAIS_FRONT + 0.4)), (north, DAIS_FRONT + 0.4 + L, HEARTH_A - BREAST_HALF - 1.2), (north, HEARTH_A + BREAST_HALF + 1.2, 2 * L))
for F, a0, a1 in BENCHES:
    hall.fbox(F, a0, a1, 0.0, 0.4, -0.1, 0.4, ashlar)
    hall.fbox(F, a0 - (0.0 if a0 <= 0.01 else 0.03), a1 + (0.0 if a1 >= 2 * L - 0.01 else 0.03), 0.0, 0.45, 0.4, 0.47, dressed)


# --- Roof timbers ----------------------------------------------------------------------
# An open roof on five king-post trusses. From the top down: the boarding;
# common rafters; purlins and a ridge piece carrying those; and under the
# purlins the trusses: principal rafters, a king post and struts standing on a
# tie beam, which rests on the wall plates and on wall posts that come down to
# stone corbels, with a curved brace in each angle.

COS, SIN = math.cos(PITCH), math.sin(PITCH)
RAFTER, PURLIN = 0.12, 0.22  # their depths, square to the roof
PRINCIPAL_TOP, PRINCIPAL = 0.34, 0.36  # how far below the boarding a principal rafter's top is, and its depth, both straight down
TIE_FOOT, TIE_TOP = 5.72, 6.06
POST_FOOT = 4.25


def across(x, points, side=1):
    """Points given as (y, z) in the plane across the hall at x."""
    return [(x, side * y, z) for y, z in points]


for x in TRUSSES:
    trim.box((x - 0.14, -W - 0.25, TIE_FOOT), (x + 0.14, W + 0.25, TIE_TOP), dark_wood, grain=(0, 1, 0))
    trim.box((x - 0.13, -0.13, TIE_TOP), (x + 0.13, 0.13, RIDGE - 0.5), dark_wood, grain=UP)  # the king post
    foot = [(-0.25, TIE_TOP), (0.25, TIE_TOP), (0.13, TIE_TOP + 0.42), (-0.13, TIE_TOP + 0.42)]
    trim.prism(across(x - 0.15, foot), (0.3, 0, 0), dark_wood, back=True, grain=UP)
    head_z = RIDGE - PRINCIPAL_TOP - PRINCIPAL
    head = [(-0.13, head_z - 0.75), (-0.26, head_z - 0.45), (-0.26, head_z + 0.1), (0.26, head_z + 0.1), (0.26, head_z - 0.45), (0.13, head_z - 0.75)]
    trim.prism(across(x - 0.15, head), (0.3, 0, 0), dark_wood, back=True, grain=UP)
    for side in (1, -1):
        # Wall post, on its corbel.
        lo, hi = sorted((side * (W - 0.22), side * (W + 0.05)))
        trim.box((x - 0.12, lo, POST_FOOT), (x + 0.12, hi, TIE_FOOT), dark_wood, grain=UP)
        corbel = [(W + 0.02, POST_FOOT - 0.6), (W - 0.1, POST_FOOT - 0.5), (W - 0.3, POST_FOOT - 0.14), (W - 0.3, POST_FOOT), (W + 0.02, POST_FOOT)]
        trim.prism(across(x - 0.17, corbel, side), (0.34, 0, 0), dressed, back=True)
        trim.box((x - 0.2, min(side * (W - 0.34), side * W), POST_FOOT), (x + 0.2, max(side * (W - 0.34), side * W), POST_FOOT + 0.07), dressed)
        # The brace: a quarter of an ellipse from the post up to the tie beam.
        reach, rise = 2.3, TIE_FOOT - (POST_FOOT + 0.2)
        cy, cz = W - 0.22 - reach, POST_FOOT + 0.2
        soffit, back = [], []
        SEGMENTS = 10
        for k in range(SEGMENTS + 1):
            t = math.radians(90 * k / SEGMENTS)
            p = Vector((cy + reach * math.cos(t), cz + rise * math.sin(t)))
            normal = Vector((math.cos(t) / reach, math.sin(t) / rise)).normalized()
            soffit.append(p)
            back.append(p + normal * 0.21)
        for k in range(SEGMENTS):
            piece = [tuple(soffit[k]), tuple(soffit[k + 1]), tuple(back[k + 1]), tuple(back[k])]
            run = soffit[k + 1] - soffit[k]
            trim.prism(across(x - 0.1, piece, side), (0.2, 0, 0), dark_wood, back=True, sides=[True, False, True, False], grain=(0, side * run.x, run.y))
        # Principal rafter, standing on the tie beam.
        top_foot = (RIDGE - PRINCIPAL_TOP - TIE_TOP) / SLOPE
        bottom_foot = (RIDGE - PRINCIPAL_TOP - PRINCIPAL - TIE_TOP) / SLOPE
        rafter = [(top_foot, TIE_TOP), (0.0, RIDGE - PRINCIPAL_TOP), (0.0, RIDGE - PRINCIPAL_TOP - PRINCIPAL), (bottom_foot, TIE_TOP)]
        trim.prism(across(x - 0.13, rafter, side), (0.26, 0, 0), dark_wood, back=True, grain=(0, -side, SLOPE))

        def soffit_at(y):
            return under(y) - PRINCIPAL_TOP - PRINCIPAL

        # A strut from the king post's foot up to the principal.
        strut = [(0.12, TIE_TOP + 0.2), (0.12, TIE_TOP + 0.48), (2.55, soffit_at(2.55) + 0.03), (2.8, soffit_at(2.8) + 0.03)]
        trim.prism(across(x - 0.09, strut, side), (0.18, 0, 0), dark_wood, back=True, grain=(0, side * 2.6, 1.3))

for side in (1, -1):
    slope_up = Vector((0.0, -side * COS, SIN))  # up the slope, toward the ridge
    square = Vector((0.0, -side * SIN, -COS))  # square to the roof, into the room
    # Purlins, their backs let into the principals.
    for y in (1.75, 3.55):
        centre = Vector((0.0, side * y, under(y)))
        top = RAFTER - 0.006  # a hair up into the rafters, so that no two faces lie in one plane
        section = [centre + square * top - slope_up * 0.1, centre + square * top + slope_up * 0.1, centre + square * (RAFTER + PURLIN) + slope_up * 0.1, centre + square * (RAFTER + PURLIN) - slope_up * 0.1]
        trim.prism([p + Vector((-L - 0.1, 0, 0)) for p in section], (2 * L + 0.2, 0, 0), dark_wood, front=False, grain=(1, 0, 0))
        # Wind braces: a curved piece in the plane of the roof from each principal up to the purlin, either side.
        for x in TRUSSES:
            for way in (-1, 1):
                foot = centre + square * (RAFTER + 0.05) - slope_up * 1.25
                points = []
                for k in range(6):
                    t = math.radians(90 * k / 5)
                    points.append(foot + Vector((x + way * (0.13 + 1.3 * (1 - math.cos(t))), 0, 0)) + slope_up * 1.15 * math.sin(t))
                for p, q in zip(points[:-1], points[1:]):
                    along = (q - p).normalized()
                    wide = along.cross(square).normalized() * 0.08
                    trim.prism([p - wide, q - wide, q + wide, p + wide], square * 0.09, dark_wood, back=True, sides=[True, False, True, False], grain=along)
    # Common rafters.
    drop = RAFTER / COS
    reach = W + 0.12
    section = [(reach, under(reach)), (0.0, RIDGE), (0.0, RIDGE - drop), (reach, under(reach) - drop)]
    for k in range(int(2 * L / 0.5)):
        x = -L + 0.25 + 0.5 * k
        trim.prism(across(x - 0.045, section, side), (0.09, 0, 0), dark_wood, back=True, sides=[False, False, True, False], grain=(0, -side, SLOPE))
    # The wall plate the rafters' feet stand on.
    lo, hi = sorted((side * (W - 0.2), side * (W + 0.05)))
    trim.box((-L - 0.05, lo, EAVES - 0.22), (L + 0.05, hi, EAVES + 0.02), dark_wood, grain=(1, 0, 0))
trim.box((-L - 0.1, -0.09, RIDGE - drop - 0.3), (L + 0.1, 0.09, RIDGE - drop + 0.02), dark_wood, grain=(1, 0, 0))  # the ridge piece
# A table of small stone corbels under each wall plate, between the trusses and clear of the window heads.
plate_foot = EAVES - 0.22
for F, windows in ((south, [L - x for x in BAYS]), (north, [x + L for x in BAYS if x != HEARTH])):
    for k in range(int(2 * L / 0.5)):
        a = 0.25 + 0.5 * k
        if any(abs(a - (x + L)) < 0.3 for x in TRUSSES) or any(abs(a - w) < 0.3 for w in windows):
            continue
        profile = [(0.0, plate_foot - 0.3), (0.15, plate_foot - 0.12), (0.15, plate_foot), (0.0, plate_foot)]
        trim.prism([F.at(a - 0.08, d, z) for d, z in profile], F.U * 0.16, dressed, back=True, sides=[True, True, False, False])


# --- Courses, panelling, cloth --------------------------------------------------------


def course(F, spans, profile, mat):
    """A moulding along a wall: `profile` is [(out, height), ...] from the wall and back to it."""
    for a0, a1 in spans:
        trim.prism([F.at(a0, d, z) for d, z in profile], F.U * (a1 - a0), mat, back=True, sides=[True] * (len(profile) - 1) + [False])


STRING = LANCET[1][1] - 0.09  # the top of the string course, which the window sills sit on and the panelling stops under
string_profile = [(0.0, STRING - 0.15), (0.055, STRING - 0.11), (0.085, STRING - 0.03), (0.085, STRING), (0.0, STRING)]
door_gap = (W - DOOR[0] / 2 - 0.34, W + DOOR[0] / 2 + 0.34)
course(south, [(0.0, 2 * L)], string_profile, dressed)
course(north, [(0.0, HEARTH_A - BREAST_HALF), (HEARTH_A + BREAST_HALF, 2 * L)], string_profile, dressed)
course(east, [(0.0, door_gap[0]), (door_gap[1], 2 * W)], string_profile, dressed)
plinth = [(0.0, 0.0), (0.12, 0.0), (0.12, 0.46), (0.05, 0.56), (0.0, 0.56)]
course(east, [(0.4, door_gap[0]), (door_gap[1], 2 * W - 0.4)], plinth, ashlar)
course(north, [(HEARTH_A - BREAST_HALF - 1.2, HEARTH_A - BREAST_HALF), (HEARTH_A + BREAST_HALF, HEARTH_A + BREAST_HALF + 1.2)], plinth, ashlar)


def panelling(F, a0, a1, z0, z1):
    """Oak panelling: boards behind a frame of rails and stiles."""
    trim.fbox(F, a0, a1, 0.0, 0.022, z0, z1, wood, grain=UP)
    trim.fbox(F, a0, a1, 0.0, 0.07, z0, z0 + 0.17, dark_wood)
    trim.fbox(F, a0, a1, 0.0, 0.06, z1 - 0.13, z1, dark_wood)
    middle = z0 + (z1 - z0) * 0.55
    trim.fbox(F, a0, a1, 0.0, 0.05, middle - 0.05, middle + 0.05, dark_wood)
    count = max(1, round((a1 - a0) / 0.62))
    for k in range(count + 1):
        a = min(max(a0 + (a1 - a0) * k / count, a0 + 0.045), a1 - 0.045)
        trim.fbox(F, a - 0.045, a + 0.045, 0.0, 0.056, z0 + 0.17, z1 - 0.13, dark_wood)


# Round the dais: up to the string course along the sides, and higher across the end wall, where no window comes down so low.
panelling(west, 0.0, 2 * W, DAIS, 2.2)
panelling(south, L - DAIS_FRONT, 2 * L, DAIS, STRING - 0.15)
panelling(north, 0.0, DAIS_FRONT + L, DAIS, STRING - 0.15)

# A cloth of estate behind the high seat, under a little canopy.
CLOTH_HALF, CLOTH_TOP = 1.35, 2.95
trim.fbox(west, W - CLOTH_HALF, W + CLOTH_HALF, 0.09, 0.105, DAIS + 0.04, CLOTH_TOP, cloth_red)
for side in (-1, 1):
    a0, a1 = sorted((W + side * CLOTH_HALF, W + side * (CLOTH_HALF + 0.14)))
    trim.fbox(west, a0, a1, 0.09, 0.105, DAIS + 0.04, CLOTH_TOP, cloth_gold)
trim.fbox(west, W - 0.09, W + 0.09, 0.105, 0.112, DAIS + 0.04, CLOTH_TOP, cloth_gold)
trim.fbox(west, W - CLOTH_HALF - 0.2, W + CLOTH_HALF + 0.2, 0.0, 0.62, CLOTH_TOP, CLOTH_TOP + 0.12, dark_wood)
trim.fbox(west, W - CLOTH_HALF - 0.2, W + CLOTH_HALF + 0.2, 0.6, 0.615, CLOTH_TOP - 0.22, CLOTH_TOP, cloth_red, back=True)
for side in (-1, 1):
    a = W + side * (CLOTH_HALF + 0.2)
    kit.block(trim, west.at(a, 0.0, CLOTH_TOP - 0.22), west.at(a - side * 0.015, 0.6, CLOTH_TOP), cloth_red)
for k in range(9):  # a gold fringe of points along its edge
    a = W - CLOTH_HALF - 0.2 + (2 * CLOTH_HALF + 0.4) * k / 9
    step = (2 * CLOTH_HALF + 0.4) / 9
    trim.fprism(west, [(a, CLOTH_TOP - 0.22), (a + step, CLOTH_TOP - 0.22), (a + step / 2, CLOTH_TOP - 0.36)], 0.6, 0.615, cloth_gold, back=True)

# A runner up the middle of the hall, from the door to the dais.
RUNNER = (DAIS_FRONT + 0.75, L - 1.6)
trim.box((RUNNER[0], -0.7, 0.0), (RUNNER[1], 0.7, 0.018), cloth_red)
for side in (-1, 1):
    lo, hi = sorted((side * 0.7, side * 0.86))
    trim.box((RUNNER[0], lo, 0.0), (RUNNER[1], hi, 0.018), cloth_gold)


# --- Weathering -----------------------------------------------------------------------


def weather(mesh):
    """
    Paints the structure's vertex colours, as the town's wall is painted. The
    walling's material is multiplied by them in Blender; in the game every
    material on the mesh is, so each kind of surface is painted for itself:

    - stone: lighter and darker stretches that do not repeat, damp toward the
      floor, and smoke-darkened toward the roof, most of all over the fire;
    - the floor: worn pale up the middle of the hall, dirtier along the walls
      and in the corners, ash and soot in front of the hearth;
    - timber: darker with smoke toward the ridge, and nothing else;
    - iron: left alone.
    """
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    x, y, z = co[:, 0], co[:, 1], co[:, 2]
    along = x + y  # every wall runs along X or Y, so their sum runs along it too
    patches = fbm(along + 900.0, z * 2.5 + 300.0, octaves=3, frequency=1 / 7)
    damp = (1 - smoothstep(0.2, 1.7, z)) * (0.5 + 0.5 * value_noise(along * 0.21 + 40.0, z * 0.5 + 9.0))
    smoke = smoothstep(3.2, 8.5, z) * (0.55 + 0.45 * value_noise(along * 0.33 + 12.0, z * 0.6 + 5.0))
    by_fire = np.exp(-(((x - HEARTH) / 3.5) ** 2)) * smoothstep(0.0, 3.0, y) * smoothstep(1.5, 5.5, z)
    light = (0.84 + 0.32 * patches) * (1 - 0.24 * damp) * (1 - 0.3 * smoke) * (1 - 0.25 * by_fire)
    warm = np.array([1.0, 0.97, 0.93])
    cold = np.array([0.80, 0.86, 0.80])
    stone = light[:, None] * (warm * (1 - 0.6 * damp[:, None]) + cold * 0.6 * damp[:, None])

    trodden = 1 - smoothstep(0.7, 2.4, np.abs(y))
    from_wall = np.minimum(L - np.abs(x), W - np.abs(y))
    ash = np.exp(-(((x - HEARTH) / 2.2) ** 2 + ((y - (W - 2.0)) / 1.3) ** 2))
    floor = (0.7 + 0.2 * fbm(x * 3.0 + 50.0, y * 3.0 + 80.0, octaves=3, frequency=1 / 9) + 0.14 * trodden) * (0.78 + 0.22 * smoothstep(0.0, 1.6, from_wall)) * (1 - 0.3 * ash)
    floor = floor[:, None] * np.array([1.0, 0.98, 0.95])

    timber = np.repeat((1 - 0.3 * smoothstep(5.5, 10.0, z))[:, None], 3, axis=1)

    # Which of those each vertex takes: that of the material of the faces it belongs to.
    kinds = {wall_stone: 1, ashlar: 1, dressed: 1, sooty: 1, flags: 2, wood: 3, dark_wood: 3}
    kind_of_slot = np.array([kinds.get(m, 0) for m in mesh.materials])
    face_slot = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", face_slot)
    face_corners = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", face_corners)
    corner_vertex = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", corner_vertex)
    kind = np.zeros(len(co), dtype=np.int32)
    kind[corner_vertex] = np.repeat(kind_of_slot[face_slot], face_corners)

    rgb = np.ones((len(co), 3))
    for number, colours in ((1, stone), (2, floor), (3, timber)):
        rgb[kind == number] = colours[kind == number]
    rgba = np.concatenate([np.clip(rgb, 0.0, 1.0), np.ones((len(co), 1))], axis=1)
    paint = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    paint.data.foreach_set("color", rgba.astype(np.float32).ravel())
    mesh.color_attributes.active_color = paint
    mesh.color_attributes.render_color_index = 0


building = collection("Hall")
hall_mesh = hall.mesh("Hall")
weather(hall_mesh)
add("Hall", hall_mesh, building, location=ORIGIN)
add("HallTrim", trim.mesh("HallTrim"), building, location=ORIGIN, collision="none", shadows=False)
add("HallGlass", panes.mesh("HallGlass"), building, location=ORIGIN, shadows=False)


# --- Furniture: linked duplicates ---------------------------------------------------------
# Each is modelled standing at the origin; wall fittings with +X along the wall
# and -Y out of it, so `Frame.turn` turns them to any wall.

props = collection("Props")
placer = Placer(props)


def place(mesh, x, y, z=0.0, rotation_z=0.0, scale=1.0, **options):
    return placer(mesh, ORIGIN.x + x, ORIGIN.y + y, ORIGIN.z + z, rotation_z=rotation_z, scale=scale, **options)


def on_wall(mesh, F, a, z, d=0.0, **options):
    p = F.at(a, d, z)
    return place(mesh, p.x, p.y, p.z, rotation_z=F.turn, collision="none", **options)


def candle(b, x, y, z, tall=0.14, radius=0.016, lit=True):
    b.cylinder((x, y), radius, z, z + tall, wax, segments=6)
    if lit:
        b.cylinder((x, y), radius * 0.95, z + tall + 0.008, z + tall + 0.075, flame, segments=5, top_radius=0.004)


def candlestick(b, x, y, z, tall=0.22, metal=pewter):
    b.cylinder((x, y), 0.055, z, z + 0.018, metal, segments=8, top_radius=0.04)
    b.cylinder((x, y), 0.012, z + 0.018, z + tall, metal, segments=6)
    b.cylinder((x, y), 0.03, z + tall, z + tall + 0.02, metal, segments=8)
    candle(b, x, y, z + tall + 0.02)


def loaf(b, x, y, z, size=0.11):
    before = set(b.bm.faces)
    ball = bmesh.ops.create_icosphere(b.bm, subdivisions=1, radius=size)
    bmesh.ops.scale(b.bm, vec=(1.0, 0.8, 0.55), verts=ball["verts"])
    bmesh.ops.translate(b.bm, vec=(x, y, z + size * 0.4), verts=ball["verts"])
    b.finish(set(b.bm.faces) - before, bread)


def lay_table(b, length, top, seed, rich=False):
    """What is on a table: platters, trenchers, bowls, cups, a jug, bread and a candle or two."""
    chance = random.Random(seed)
    cup, plate = (gold, gold) if rich else (pewter, pewter)
    places = int(length / 0.62)
    for side in (-1, 1):
        for k in range(places):
            x = (k + 0.5 - places / 2) * (length - 0.3) / places + chance.uniform(-0.05, 0.05)
            y = side * (0.27 + chance.uniform(-0.02, 0.02))
            if chance.random() < 0.2:
                continue
            if rich or chance.random() < 0.45:
                b.cylinder((x, y), 0.105, top, top + 0.012, plate, segments=10)
            else:  # a trencher: a slab of bread to eat off
                b.box((x - 0.1, y - 0.075, top), (x + 0.1, y + 0.075, top + 0.018), bread)
            cx, cy = x + chance.choice((-0.19, 0.19)), y - side * chance.uniform(0.0, 0.08)
            b.cylinder((cx, cy), 0.036, top, top + (0.15 if rich else 0.11), cup, segments=8, top_radius=0.042 if rich else 0.034)
    for k in range(3 if length > 3 else 2):  # down the middle: the dishes everyone reaches for
        x = (k + 0.5 - (3 if length > 3 else 2) / 2) * length / (3 if length > 3 else 2) + chance.uniform(-0.12, 0.12)
        kind = (k + seed) % 3
        if kind == 0:
            b.cylinder((x, 0.0), 0.2, top, top + 0.016, plate, segments=12)
            for _ in range(3):
                loaf(b, x + chance.uniform(-0.09, 0.09), chance.uniform(-0.08, 0.08), top + 0.016, chance.uniform(0.07, 0.1))
        elif kind == 1:
            b.cylinder((x, 0.0), 0.1, top, top + 0.085, clay, segments=10, top_radius=0.16)
            b.cylinder((x, 0.0), 0.148, top + 0.07, top + 0.078, bread, segments=10)
            jx = x + chance.choice((-0.33, 0.33))
            b.cylinder((jx, 0.03), 0.075, top, top + 0.17, clay, segments=10, top_radius=0.085)
            b.cylinder((jx, 0.03), 0.085, top + 0.17, top + 0.25, clay, segments=10, top_radius=0.045)
            b.cylinder((jx, 0.03), 0.045, top + 0.25, top + 0.28, clay, segments=10, top_radius=0.055)
        else:
            loaf(b, x, 0.02, top, 0.14)
            b.box((x + 0.2, -0.09, top), (x + 0.48, 0.09, top + 0.02), wood, grain="long")
            loaf(b, x + 0.34, 0.0, top + 0.02, 0.075)
    for x in ((-length * 0.3, length * 0.3) if length > 3 else (0.0,)):
        candlestick(b, x + chance.uniform(-0.05, 0.05), chance.choice((-0.1, 0.1)), top, metal=gold if rich else pewter)


# Trestle tables, two of them so that neighbours are laid differently.
TABLE_LONG, TABLE_WIDE, TABLE_TOP = 3.2, 0.86, 0.78
table_meshes = []
for variant in range(2):
    b = Builder([wood])
    for k in range(3):  # three boards
        y0 = -TABLE_WIDE / 2 + k * TABLE_WIDE / 3
        b.box((-TABLE_LONG / 2, y0 + 0.004, TABLE_TOP - 0.055), (TABLE_LONG / 2, y0 + TABLE_WIDE / 3 - 0.004, TABLE_TOP), wood, grain="long")
    for x in (-1.15, 1.15):
        b.box((x - 0.05, -0.38, TABLE_TOP - 0.15), (x + 0.05, 0.38, TABLE_TOP - 0.055), dark_wood, grain="long")
        for side in (-1, 1):  # splayed legs
            leg = [(side * 0.2, TABLE_TOP - 0.15), (side * 0.3, TABLE_TOP - 0.15), (side * 0.42, 0.0), (side * 0.32, 0.0)]
            b.prism([(x - 0.045, y, z) for y, z in leg], (0.09, 0, 0), dark_wood, back=True, grain=UP)
        b.box((x - 0.035, -0.33, 0.2), (x + 0.035, 0.33, 0.28), dark_wood, grain="long")
    b.box((-1.3, -0.04, 0.3), (1.3, 0.04, 0.4), dark_wood, grain="long")  # the rail that ties the trestles
    for x in (-1.26, 1.22):  # and its wedges
        b.box((x, -0.015, 0.26), (x + 0.04, 0.015, 0.46), wood)
    lay_table(b, TABLE_LONG, TABLE_TOP, 3 + variant * 5)
    table_meshes.append(b.mesh(f"TrestleTable{variant}"))

b = Builder([wood])
b.box((-1.45, -0.15, 0.41), (1.45, 0.15, 0.46), wood, grain="long")
for x in (-1.2, 1.2):
    b.box((x - 0.025, -0.14, 0.0), (x + 0.025, 0.14, 0.41), dark_wood, grain=UP)
b.box((-1.2, -0.02, 0.2), (1.2, 0.02, 0.29), dark_wood, grain="long")
bench_mesh = b.mesh("HallBench")

# The high table: a cloth to the floor on the hall's side, and the best plate.
HIGH_LONG, HIGH_WIDE, HIGH_TOP = 2.7, 0.95, 0.82
b = Builder([linen])
b.box((-HIGH_LONG / 2, -HIGH_WIDE / 2, HIGH_TOP - 0.03), (HIGH_LONG / 2, HIGH_WIDE / 2, HIGH_TOP), linen)
b.box((-HIGH_LONG / 2 - 0.01, -HIGH_WIDE / 2 - 0.012, 0.08), (HIGH_LONG / 2 + 0.01, -HIGH_WIDE / 2, HIGH_TOP - 0.005), linen)
for x in (-HIGH_LONG / 2 - 0.012, HIGH_LONG / 2):
    b.box((x, -HIGH_WIDE / 2 - 0.012, 0.3), (x + 0.012, HIGH_WIDE / 2, HIGH_TOP - 0.005), linen)
b.box((-HIGH_LONG / 2 - 0.012, -HIGH_WIDE / 2 - 0.016, 0.08), (HIGH_LONG / 2 + 0.012, -HIGH_WIDE / 2 - 0.004, 0.16), cloth_red)
b.box((-0.3, -HIGH_WIDE / 2 - 0.018, 0.16), (0.3, HIGH_WIDE / 2, HIGH_TOP + 0.004), cloth_red)  # a runner over it
for x in (-1.1, 1.1):
    for y in (-0.36, 0.36):
        b.box((x - 0.045, y - 0.045, 0.0), (x + 0.045, y + 0.045, HIGH_TOP - 0.03), dark_wood, grain=UP)
b.box((-1.1, 0.33, 0.25), (1.1, 0.39, 0.33), dark_wood, grain="long")
lay_table(b, HIGH_LONG, HIGH_TOP + 0.004, 21, rich=True)
high_table_mesh = b.mesh("HighTable")


def chair(name, wide, deep, back_tall, cushion=None, canopy=False):
    """A boarded chair, facing -Y."""
    b = Builder([dark_wood])
    seat = 0.48
    b.box((-wide / 2, -deep / 2, seat - 0.05), (wide / 2, deep / 2, seat), dark_wood, grain="long")
    b.box((-wide / 2, -deep / 2 + 0.02, 0.06), (wide / 2, -deep / 2 + 0.05, seat - 0.05), dark_wood, grain="long")  # boarded in below the seat
    for x in (-wide / 2, wide / 2 - 0.05):
        b.box((x, -deep / 2, 0.0), (x + 0.05, deep / 2, seat + 0.24), dark_wood, grain=UP)  # the sides, to arm height
        b.box((x - 0.01, -deep / 2 - 0.02, seat + 0.24), (x + 0.06, deep / 2, seat + 0.28), dark_wood, grain=(0, 1, 0))
    b.box((-wide / 2, deep / 2 - 0.05, 0.0), (wide / 2, deep / 2, back_tall), dark_wood, grain=UP)
    b.box((-wide / 2 - 0.02, deep / 2 - 0.06, back_tall), (wide / 2 + 0.02, deep / 2 + 0.01, back_tall + 0.06), dark_wood, grain="long")
    panel = wood
    b.box((-wide / 2 + 0.09, deep / 2 - 0.062, seat + 0.1), (wide / 2 - 0.09, deep / 2 - 0.05, back_tall - 0.1), panel, grain=UP)
    if cushion is not None:
        b.box((-wide / 2 + 0.06, -deep / 2 + 0.01, seat), (wide / 2 - 0.06, deep / 2 - 0.06, seat + 0.06), cushion)
    if canopy:  # finials on the back posts and a pointed head to the back
        for x in (-wide / 2 - 0.01, wide / 2 - 0.05):
            b.box((x, deep / 2 - 0.06, 0.0), (x + 0.06, deep / 2 + 0.01, back_tall + 0.2), dark_wood, grain=UP)
            b.cylinder((x + 0.03, deep / 2 - 0.025), 0.045, back_tall + 0.2, back_tall + 0.3, gold, segments=6, top_radius=0.005)
        gable_ = [(-wide / 2 + 0.05, back_tall + 0.06), (wide / 2 - 0.05, back_tall + 0.06), (0.0, back_tall + 0.42)]
        b.prism([(x, deep / 2 - 0.05, z) for x, z in gable_], (0, 0.05, 0), dark_wood, back=True, grain=UP)
        b.box((-0.11, deep / 2 - 0.058, back_tall - 0.34), (0.11, deep / 2 - 0.05, back_tall - 0.12), gold)
    return b.mesh(name)


chair_mesh = chair("HallChair", 0.6, 0.52, 1.25, cushion=cloth_blue)
high_seat_mesh = chair("HighSeat", 0.86, 0.62, 1.85, cushion=cloth_red, canopy=True)

# A chest with a rounded lid, bound in iron.
b = Builder([wood])
CHEST_X, CHEST_Y, CHEST_Z = 0.56, 0.29, 0.44
b.box((-CHEST_X, -CHEST_Y, 0.05), (CHEST_X, CHEST_Y, CHEST_Z), wood, grain="long")
for x in (-CHEST_X, CHEST_X - 0.08):
    for y in (-CHEST_Y, CHEST_Y - 0.08):
        b.box((x - 0.01, y - 0.01, 0.0), (x + 0.09, y + 0.09, 0.07), dark_wood)


def lid(extra):
    return [((CHEST_Y + extra) * math.cos(math.radians(180 - 180 * k / 8)), CHEST_Z + (0.2 + extra) * math.sin(math.radians(180 * k / 8))) for k in range(9)]


b.prism([(-CHEST_X, y, z) for y, z in lid(0.0)], (2 * CHEST_X, 0, 0), wood, back=True, grain=(1, 0, 0))
for x in (-0.42, -0.035, 0.35):
    b.prism([(x, y, z) for y, z in lid(0.012)], (0.07, 0, 0), iron, back=True)
    b.box((x, -CHEST_Y - 0.012, 0.05), (x + 0.07, CHEST_Y + 0.012, CHEST_Z), iron)
b.box((-0.07, -CHEST_Y - 0.03, CHEST_Z - 0.16), (0.07, -CHEST_Y - 0.012, CHEST_Z + 0.03), iron)
for side in (-1, 1):
    b.rod((side * (CHEST_X + 0.025), -0.1, 0.3), (side * (CHEST_X + 0.025), 0.1, 0.3), 0.014, iron, segments=5)
chest_mesh = b.mesh("Chest")

# A barrel that bulges, hooped in iron.
b = Builder([wood])
for z0, z1, r0, r1 in ((0.0, 0.3, 0.3, 0.37), (0.3, 0.65, 0.37, 0.37), (0.65, 0.95, 0.37, 0.3)):
    b.cylinder((0, 0), r0, z0, z1, wood, segments=14, top_radius=r1)
for z, radius in ((0.06, 0.322), (0.27, 0.372), (0.63, 0.378), (0.84, 0.332)):
    b.cylinder((0, 0), radius, z, z + 0.05, iron, segments=14)
b.cylinder((0, 0), 0.27, 0.95, 0.955, dark_wood, segments=14)
barrel_mesh = b.mesh("HallBarrel")

# A brazier on three legs, with a bed of coals. The fire is the game's (an `fx` marker over each).
b = Builder([iron])
b.cylinder((0, 0), 0.15, 0.72, 0.95, iron, segments=12, top_radius=0.36)
b.cylinder((0, 0), 0.375, 0.93, 0.97, iron, segments=12)
b.cylinder((0, 0), 0.32, 0.955, 0.985, embers, segments=12)
for k in range(3):
    angle = math.tau * k / 3 + 0.5
    b.rod((0.14 * math.cos(angle), 0.14 * math.sin(angle), 0.76), (0.34 * math.cos(angle), 0.34 * math.sin(angle), 0.0), 0.022, iron, segments=5)
for k in range(3):
    p, q = math.tau * k / 3 + 0.5, math.tau * (k + 1) / 3 + 0.5
    b.rod((0.25 * math.cos(p), 0.25 * math.sin(p), 0.34), (0.25 * math.cos(q), 0.25 * math.sin(q), 0.34), 0.014, iron, segments=4)
brazier_mesh = b.mesh("HallBrazier")
BRAZIER_FIRE = 1.0  # where its flames start

# An iron chandelier: a ring of candles on chains. It hangs from its origin.
b = Builder([iron])
RING, RING_Z, HUB_Z = 0.72, -2.1, -1.25
b.rod((0, 0, 0.0), (0, 0, HUB_Z), 0.016, iron, segments=5)
b.cylinder((0, 0), 0.05, HUB_Z - 0.06, HUB_Z, iron, segments=8)
RING_PARTS = 16
for k in range(RING_PARTS):
    p, q = math.tau * k / RING_PARTS, math.tau * (k + 1) / RING_PARTS
    inner = [(math.cos(t) * (RING - 0.025), math.sin(t) * (RING - 0.025)) for t in (p, q)]
    outer = [(math.cos(t) * (RING + 0.025), math.sin(t) * (RING + 0.025)) for t in (p, q)]
    lo, hi = RING_Z - 0.05, RING_Z + 0.05
    middle = Vector((math.cos((p + q) / 2), math.sin((p + q) / 2), 0.0))
    b.poly([(*outer[0], lo), (*outer[1], lo), (*outer[1], hi), (*outer[0], hi)], iron, middle)
    b.poly([(*inner[0], lo), (*inner[1], lo), (*inner[1], hi), (*inner[0], hi)], iron, -middle)
    b.poly([(*inner[0], hi), (*inner[1], hi), (*outer[1], hi), (*outer[0], hi)], iron, UP)
    b.poly([(*inner[0], lo), (*inner[1], lo), (*outer[1], lo), (*outer[0], lo)], iron, -UP)
for k in range(4):
    angle = math.tau * k / 4 + math.pi / 4
    x, y = math.cos(angle) * RING, math.sin(angle) * RING
    b.rod((0, 0, HUB_Z - 0.03), (x, y, RING_Z + 0.05), 0.012, iron, segments=4)
    b.rod((0, 0, RING_Z), (x, y, RING_Z), 0.016, iron, segments=4)
for k in range(8):
    angle = math.tau * k / 8
    x, y = math.cos(angle) * RING, math.sin(angle) * RING
    b.cylinder((x, y), 0.065, RING_Z + 0.05, RING_Z + 0.066, iron, segments=8, top_radius=0.075)
    candle(b, x, y, RING_Z + 0.066, tall=0.17, radius=0.022)
chandelier_mesh = b.mesh("Chandelier")

# A candle on an iron arm, for the walls.
b = Builder([iron])
b.box((-0.05, -0.012, -0.14), (0.05, 0.0, 0.14), iron)
b.rod((0, 0, -0.06), (0, -0.24, 0.0), 0.013, iron, segments=5)
b.cylinder((0, -0.24), 0.06, 0.0, 0.02, iron, segments=8, top_radius=0.07)
candle(b, 0.0, -0.24, 0.02, tall=0.2, radius=0.026)
sconce_mesh = b.mesh("Sconce")

b = Builder([pewter])
candlestick(b, 0.0, 0.0, 0.0, tall=0.3)
candlestick_mesh = b.mesh("Candlestick")


# A standing candlestick of iron, as tall as a man, for either side of the high seat.
b = Builder([iron])
for k in range(3):
    angle = math.tau * k / 3 + 0.3
    b.rod((0, 0, 0.22), (0.26 * math.cos(angle), 0.26 * math.sin(angle), 0.0), 0.018, iron, segments=5)
b.rod((0, 0, 0.18), (0, 0, 1.5), 0.02, iron, segments=6)
for z in (0.55, 1.05):
    b.cylinder((0, 0), 0.04, z, z + 0.05, iron, segments=6)
b.rod((-0.24, 0, 1.42), (0.24, 0, 1.42), 0.014, iron, segments=5)
for x, lift in ((-0.24, 0.0), (0.0, 0.14), (0.24, 0.0)):
    b.rod((x, 0, 1.42), (x, 0, 1.5 + lift), 0.012, iron, segments=5)
    b.cylinder((x, 0), 0.05, 1.5 + lift, 1.52 + lift, iron, segments=8, top_radius=0.062)
    candle(b, x, 0.0, 1.52 + lift, tall=0.2, radius=0.022)
candelabrum_mesh = b.mesh("Candelabrum")

# A cup-board: the household's plate set out on stepped shelves under a cloth. It faces -Y.
b = Builder([dark_wood])
b.box((-0.85, -0.28, 0.0), (0.85, 0.28, 0.92), dark_wood, grain="long")
for x in (-0.85, -0.3, 0.25, 0.8):  # a framed front
    b.box((x, -0.3, 0.0), (x + 0.05, -0.28, 0.92), wood, grain=UP)
for z in (0.0, 0.84):
    b.box((-0.85, -0.3, z), (0.85, -0.28, z + 0.08), wood, grain="long")
b.box((-0.9, -0.32, 0.92), (0.9, 0.3, 0.945), linen)
b.box((-0.9, -0.325, 0.74), (0.9, -0.32, 0.945), linen)
for k, (depth, z) in enumerate(((0.0, 0.945), (0.14, 1.13), (0.22, 1.31))):
    if k:
        b.box((-0.8, depth - 0.12, 0.945), (0.8, 0.28, z), linen)
    for i in range(5 - k):
        x = (i - (4 - k) / 2) * 0.34
        y = (depth - 0.02 if k else -0.14) + (0.0 if (i + k) % 2 else 0.05)
        if (i + k) % 3 == 0:  # a ewer
            b.cylinder((x, y), 0.05, z, z + 0.1, gold, segments=8, top_radius=0.075)
            b.cylinder((x, y), 0.075, z + 0.1, z + 0.2, gold, segments=8, top_radius=0.035)
            b.cylinder((x, y), 0.035, z + 0.2, z + 0.25, gold, segments=8, top_radius=0.05)
        elif (i + k) % 3 == 1:  # a cup on a foot
            b.cylinder((x, y), 0.045, z, z + 0.012, gold, segments=8)
            b.cylinder((x, y), 0.012, z + 0.012, z + 0.08, gold, segments=6)
            b.cylinder((x, y), 0.03, z + 0.08, z + 0.17, gold, segments=8, top_radius=0.05)
        else:  # a dish stood on its edge against the step behind
            b.rod((x, y + 0.06, z + 0.13), (x, y + 0.045, z + 0.125), 0.13, pewter if k == 0 else gold, segments=12)
sideboard_mesh = b.mesh("Sideboard")


def banner(name, field, charge, device):
    """A banner hung flat on the wall from a pole: a border, a field, a swallow tail, and a device on it."""
    b = Builder([field])
    half, top, tall, off = 0.42, -0.06, 1.45, -0.07
    b.rod((-half - 0.14, off, 0.0), (half + 0.14, off, 0.0), 0.022, dark_wood, segments=6)
    for side in (-1, 1):
        b.rod((side * (half + 0.08), 0.0, 0.0), (side * (half + 0.08), off, 0.0), 0.014, iron, segments=4)
        b.cylinder((side * (half + 0.15), off), 0.035, -0.035, 0.035, gold, segments=6)

    def cloth(points, mat, lift=0.0):
        b.prism([(x, off - lift, z) for x, z in points], (0, -0.008, 0), mat, sides=False)

    def foot(x):
        """The bottom edge: a swallow tail, longest at the sides."""
        return top - tall + 0.14 - 0.36 * abs(x) / half

    border = 0.07
    for x0, x1, mat in ((-half, -half + border, charge), (-half + border, 0.0, field), (0.0, half - border, field), (half - border, half, charge)):
        cloth([(x0, top), (x1, top), (x1, foot(x1)), (x0, foot(x0))], mat)
    cz = top - tall * 0.45
    if device == "chevron":
        for side in (-1, 1):
            cloth([(0.0, cz + 0.27), (0.0, cz + 0.1), (side * 0.33, cz - 0.24), (side * 0.33, cz - 0.07)], charge, lift=0.005)
    elif device == "cross":
        cloth([(-0.07, cz - 0.36), (0.07, cz - 0.36), (0.07, cz + 0.36), (-0.07, cz + 0.36)], charge, lift=0.005)
        cloth([(-0.33, cz + 0.02), (0.33, cz + 0.02), (0.33, cz + 0.16), (-0.33, cz + 0.16)], charge, lift=0.008)
    else:  # a lozenge
        cloth([(0.0, cz - 0.33), (0.24, cz), (0.0, cz + 0.33), (-0.24, cz)], charge, lift=0.005)
    return b.mesh(name)


banner_meshes = [banner("BannerRed", cloth_red, cloth_gold, "chevron"), banner("BannerBlue", cloth_blue, cloth_gold, "cross"), banner("BannerGold", cloth_gold, cloth_red, "lozenge")]


def shield(name, field, charge, device):
    """A shield hung on the wall: its top edge at the origin's height."""
    b = Builder([field])
    edge = [(-0.3, 0.0), (0.3, 0.0), (0.3, -0.3), (0.2, -0.55), (0.0, -0.74), (-0.2, -0.55), (-0.3, -0.3)]
    b.prism([(x, 0.0, z) for x, z in edge], (0, -0.04, 0), field)
    rim = [(x * 1.06, z * 1.05 + 0.02) for x, z in edge]
    b.prism([(x, 0.0, z) for x, z in rim], (0, -0.025, 0), iron)
    if device == "pale":
        b.prism([(-0.07, -0.04, -0.005), (0.07, -0.04, -0.005), (0.07, -0.04, -0.66), (0.0, -0.04, -0.73), (-0.07, -0.04, -0.66)], (0, -0.006, 0), charge, sides=False)
    else:
        b.prism([(-0.295, -0.04, -0.2), (0.295, -0.04, -0.2), (0.295, -0.04, -0.34), (-0.295, -0.04, -0.34)], (0, -0.006, 0), charge, sides=False)
    b.rod((0.0, -0.04, -0.3), (0.0, -0.09, -0.3), 0.07, gold, segments=8, end_radius=0.02)  # the boss
    return b.mesh(name)


shield_meshes = [shield("ShieldRed", cloth_red, cloth_gold, "pale"), shield("ShieldBlue", cloth_blue, linen, "fess")]

# Split logs, stacked by the fire.
b = Builder([wood])
chance = random.Random(4)
for row, count in enumerate((5, 4, 3)):
    for k in range(count):
        x = (k - (count - 1) / 2) * 0.2 + chance.uniform(-0.015, 0.015)
        z = 0.095 + row * 0.165
        b.rod((x, -0.3 + chance.uniform(-0.03, 0.03), z), (x, 0.3 + chance.uniform(-0.03, 0.03), z), chance.uniform(0.08, 0.098), dark_wood if (k + row) % 3 else wood, segments=7)
logs_mesh = b.mesh("LogPile")


# --- Placing it -------------------------------------------------------------------------

# Two rows of tables down the hall with a bench either side, the middle left
# clear from the door to the dais, and the floor in front of the fire left open.
ROW = 2.65
TABLES_AT = (-5.2, -1.5, 2.2, 5.9)
for row, side in enumerate((-1, 1)):
    for k, x in enumerate(TABLES_AT):
        if side > 0 and abs(x - HEARTH) < 1.5:
            continue
        slew = rng.uniform(-0.025, 0.025)
        place(table_meshes[(k + row) % 2], x, side * ROW, rotation_z=slew + (math.pi if (k + row) % 3 == 0 else 0.0), collision="box")
        for bench_side in (-1, 1):
            place(bench_mesh, x + rng.uniform(-0.12, 0.12), side * ROW + bench_side * 0.74, rotation_z=slew + rng.uniform(-0.04, 0.04), collision="box")

# The dais: the high table across it, the high seat behind under the cloth of estate, a chair either side.
EAST = math.pi / 2  # turns something made facing -Y to face down the hall
for y in (-1.4, 1.4):
    place(high_table_mesh, -9.45, y, DAIS, rotation_z=EAST, collision="box")
add("HighSeat", high_seat_mesh, props, location=ORIGIN + Vector((-10.75, 0.0, DAIS)), rotation_z=EAST, collision="box")
for y in (-2.1, -1.1, 1.1, 2.1):
    place(chair_mesh, -10.6, y, DAIS, rotation_z=EAST + rng.uniform(-0.08, 0.08), collision="box")
for y in (-1.95, 1.95):
    place(candelabrum_mesh, -11.5, y, DAIS, rotation_z=EAST, collision="cylinder", collision_radius=0.1)
add("Sideboard", sideboard_mesh, props, location=ORIGIN + Vector((-10.3, -W + 0.4, DAIS)), rotation_z=math.pi, collision="box")

BRAZIERS = [(-8.75, -4.55, DAIS), (-8.75, 4.55, DAIS), (9.9, -3.5, 0.0), (9.9, 3.5, 0.0)]
for x, y, z in BRAZIERS:
    place(brazier_mesh, x, y, z, collision="cylinder", collision_radius=0.36)

for x in (-4.0, 0.0, 4.0):  # from the middle of three tie beams
    place(chandelier_mesh, x, 0.0, TIE_FOOT, rotation_z=rng.uniform(0, math.tau), collision="none")

# Banners on the piers between the windows, under each truss, where there is room; shields and candles on the end walls.
hung = 0
for F, places in ((south, [L - x for x in TRUSSES]), (north, [x + L for x in TRUSSES if abs(x - HEARTH) > BREAST_HALF + 0.8])):
    for a in places:
        on_wall(banner_meshes[hung % 3], F, a, POST_FOOT - 0.72, shadows=False)
        hung += 1
for k, a in enumerate((1.45, 2 * W - 1.45)):  # either side of the door
    on_wall(shield_meshes[k % 2], east, a, 3.5, shadows=False)
for k, a in enumerate((W - FLANK_AT, W + FLANK_AT)):  # under the windows that flank the great one
    on_wall(shield_meshes[(k + 1) % 2], west, a, 3.0, shadows=False)
on_wall(shield_meshes[0], north, HEARTH_A, under(W - FLUE) - 0.55, d=FLUE, shadows=False)  # and on the chimney
for a in (2.75, 2 * W - 2.75):
    on_wall(sconce_mesh, east, a, 2.5, shadows=False)
for a in (W - 2.42, W + 2.42):
    on_wall(sconce_mesh, west, a, 2.5, shadows=False)
for a in (HEARTH_A - BREAST_HALF + 0.1, HEARTH_A + BREAST_HALF - 0.1):  # and a candle at each end of the mantel
    on_wall(candlestick_mesh, north, a, LINTEL_TOP + 0.1, d=BREAST + 0.02, shadows=False)

# Stores at the door end: chests against the south bench, barrels in the north corner; logs either side of the fire.
place(chest_mesh, 11.3, -3.1, rotation_z=math.pi / 2 + 0.06, collision="box")
place(chest_mesh, 10.2, -4.72, rotation_z=math.pi + 0.05, collision="box")
place(chest_mesh, -11.2, 4.3, DAIS, rotation_z=EAST + 0.04, collision="box")
for x, y in ((11.45, 4.62), (10.68, 4.66), (11.5, 3.84), (11.42, -4.6)):
    place(barrel_mesh, x, y, rotation_z=rng.uniform(0, math.tau), collision="cylinder", collision_radius=0.37)
for a in (HEARTH_A - BREAST_HALF - 0.6, HEARTH_A + BREAST_HALF + 0.6):
    p = north.at(a, 0.42, 0.0)
    place(logs_mesh, p.x, p.y, rotation_z=rng.uniform(-0.06, 0.06), collision="box")


# --- Markers: what the game's code is told about --------------------------------------------


def mark(name, x, y, z, **options):
    return marker(name, ORIGIN.x + x, ORIGIN.y + y, ORIGIN.z + z, **options)


WESTWARD = -math.pi / 2  # an Empty's -Y is "forward": this turns it to face -X, up the hall toward the dais
INSIDE_DOOR = L - 1.3
mark("spawn", INSIDE_DOOR, 0.0, 0.2, facing=WESTWARD)
mark("entrance", INSIDE_DOOR, 0.0, 0.2, facing=WESTWARD)  # where the player arrives from the town
mark("exit", L - 0.2, 0.0, 1.2, facing=WESTWARD, door="back", prompt="Leave", radius=2.4)
mark("poi_dais", DAIS_FRONT - 0.5, 0.0, DAIS + 0.2, facing=-WESTWARD)
# Someone to talk to: he says what the story `steward` has him say
# (src/game/stories/steward.ink), and `npc` asks the game to stand a
# character here. At the front of the dais, facing down the hall.
mark("steward", DAIS_FRONT - 0.55, 0.0, DAIS, facing=-WESTWARD, talk="steward", prompt="Talk to the steward", radius=2.6, npc="fox")

# Fires. `campfire` is flames, smoke and a flickering light, and the game has
# lights for eight at most: these are five.
fire_at = north.at(HEARTH_A, 0.55, 0.3)
mark("fire_hearth", fire_at.x, fire_at.y, fire_at.z, fx="brazier", fx_scale=1.5)
for i, (x, y, z) in enumerate(BRAZIERS):
    mark(f"fire_brazier_{i}", x, y, z + BRAZIER_FIRE, fx="brazier", fx_scale=0.55)


# --- Save --------------------------------------------------------------------

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
if not argv:
    raise SystemExit("usage: blender -b --factory-startup --python make_town_hall.py -- <output.blend>")
path = os.path.abspath(argv[0])
os.makedirs(os.path.dirname(path), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=path)


def triangles_of(mesh):
    return sum(len(p.vertices) - 2 for p in mesh.polygons)


structure = sum(triangles_of(o.data) for o in building.objects)
print(
    f"[make_town_hall] {path}: {len(building.objects)} structure objects ({structure:,} triangles), "
    f"{len(props.objects)} placed props from {len({o.data.name for o in props.objects})} meshes ({placer.triangles():,} triangles), "
    f"{len(kit.markers().objects)} markers, {structure + placer.triangles():,} triangles in all"
)
for o in building.objects:
    print(f"[make_town_hall]   {o.name}: {triangles_of(o.data):,} triangles, {len(o.data.materials)} materials")
