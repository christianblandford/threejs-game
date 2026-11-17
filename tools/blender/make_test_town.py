"""
Builds the scaffold's second test map -- a small walled town -- and saves it
as a .blend.

    blender -b --factory-startup --python tools/blender/make_test_town.py -- maps/test_town.blend

(or `npm run map:make-town`, which also exports it). Open the result in
Blender to look around or edit it.

Where the island tests open terrain, this tests a built-up place: streets and
alleys, stairs, a bridge, walls to walk along, dense shadow casters, and the
things a level designer places for the game's code to use -- trigger zones,
things to interact with, effects, and named points.

How it is put together, which is how a hand-made level should be too:

- Terrain is one mesh painted with a colour attribute.
- Each block of houses is ONE object, so it is one draw call per material
  rather than one per house. A house is a shell with real depth where the eye
  goes: openings cut into the walls, timbers standing proud of the plaster,
  jettied storeys, and roofs with thickness, overhangs, barge boards, ridge
  tiles, dormers and chimneys. No two neighbours share colour, height and
  roof line.
- Everything repeated (lamps, barrels, stalls, trees, fences, and the houses'
  own fittings: window frames, doors, shutters, flower boxes, signs, chimney
  pots) is a linked duplicate of a single mesh, which the game draws as
  instances.
- Empties mark the places the game's code refers to by name.

Blender axes: X east, Y north, Z up. The town gate is in the south wall.
"""

import math
import os
import random
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from surfaces import surface  # noqa: E402
from plants import broadleaf_tree, bush, conifer_tree, import_model, model_variants  # noqa: E402

SIZE = 384.0  # metres across
STEP = 2.0  # terrain grid spacing
SEED = 11

WALL = 50.0  # the town wall's centre line is this far from the origin on each side
WALL_THICK = 2.4
RISER, TREAD = 0.15, 0.3  # every stair: low enough to walk up without jumping, deep enough to path over
WALL_STEPS = 34
WALL_HEIGHT = WALL_STEPS * RISER  # 5.1 m, so the stair flight lands exactly on the wall walk
GATE_HALF = 3.0  # half the gate opening's width
MOAT_INNER, MOAT_OUTER, MOAT_DEPTH = 54.0, 68.0, 2.2
WATER_LEVEL = -0.9
STOREY = 3.0
STREET_HALF = 4.0  # the main street, gate to square
LANE_HALF = 3.0  # the cross street
SQUARE_HALF = 15.0
# The hall stands on a terrace at the top of a rise at the north end of town.
HILL = 2.4
TERRACE_STEPS = 10
TERRACE_FRONT, TERRACE_BACK, TERRACE_HALF = 28.0, 46.0, 14.0
STAIR_FOOT_Y = TERRACE_FRONT - TERRACE_STEPS * TREAD
INNER_POND = (26.0, -27.0, 6.5, 1.5)  # x, y, radius, depth: a duck pond in the south-east yard
OUTER_PONDS = [(74.0, -112.0, 13.0, 2.4), (-98.0, -52.0, 11.0, 2.2)]
FARM = (-48.0, -116.0)

rng = random.Random(SEED)
np_rng = np.random.default_rng(SEED)


# --- Noise -------------------------------------------------------------------

LATTICE = np_rng.random((256, 256))


def value_noise(x, y):
    xi, yi = np.floor(x).astype(int), np.floor(y).astype(int)
    xf, yf = x - xi, y - yi
    u, v = xf * xf * (3 - 2 * xf), yf * yf * (3 - 2 * yf)
    a = LATTICE[xi & 255, yi & 255]
    b = LATTICE[(xi + 1) & 255, yi & 255]
    c = LATTICE[xi & 255, (yi + 1) & 255]
    d = LATTICE[(xi + 1) & 255, (yi + 1) & 255]
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v


def fbm(x, y, octaves=5, frequency=1 / 120, gain=0.5):
    total, amplitude, norm = 0.0, 1.0, 0.0
    for octave in range(octaves):
        total = total + amplitude * value_noise(x * frequency + octave * 17.3, y * frequency + octave * 5.1)
        norm += amplitude
        amplitude *= gain
        frequency *= 2.03
    return total / norm


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# --- Terrain -----------------------------------------------------------------
# The land comes first and everything else is set on it. Inside the walls it
# rises to the hall and rolls a little in the yards; the market square and a
# band along the wall stay level. Outside there are fields, ponds in the
# hollows and a road that winds with the ground rather than cutting through
# it. At the map's edge it falls away to meet a plane that runs to the horizon.

count = int(SIZE / STEP) + 1
axis = np.linspace(-SIZE / 2, SIZE / 2, count)
X, Y = np.meshgrid(axis, axis, indexing="xy")
square = np.maximum(np.abs(X), np.abs(Y))  # distance from the centre measured as the town is built: in squares


def sample(grid, x, y):
    """Bilinear lookup of a terrain grid at a world position."""
    fx = min(max((x + SIZE / 2) / STEP, 0), count - 1.001)
    fy = min(max((y + SIZE / 2) / STEP, 0), count - 1.001)
    ix, iy = int(fx), int(fy)
    tx, ty = fx - ix, fy - iy
    top = grid[iy, ix] * (1 - tx) + grid[iy, ix + 1] * tx
    bottom = grid[iy + 1, ix] * (1 - tx) + grid[iy + 1, ix + 1] * tx
    return float(top * (1 - ty) + bottom * ty)


def road_x(y):
    """The road south of the bridge: straight off the bridge, then winding."""
    return 16.0 * np.sin((y + 74.0) / 30.0) * smoothstep(-74.0, -100.0, y)


fields = (fbm(X, Y) - 0.42) * 26
height = fields * smoothstep(78.0, 120.0, square)

# The road keeps the land's broad rise and fall but is level from side to side.
road_centre = road_x(axis)  # per row of the grid
along_road = np.array([sample(height, float(cx), float(cy)) for cx, cy in zip(road_centre, axis)])
window = np.exp(-0.5 * (np.arange(-15, 16) * STEP / 10.0) ** 2)
road_profile = np.convolve(np.pad(along_road, 15, mode="edge"), window / window.sum(), mode="valid")
road_distance = np.abs(X - road_centre[:, None])
on_road = (1 - smoothstep(3.5, 9.0, road_distance)) * (Y < -MOAT_OUTER + 2)
height = height * (1 - on_road) + road_profile[:, None] * on_road

# Inside the walls.
inside = 1 - smoothstep(40.0, 46.0, square)  # nothing moves close to the wall
hill = HILL * smoothstep(1.0, 0.25, np.hypot(X / 30.0, (Y - 34.0) / 18.0))
yards = (fbm(X * 5, Y * 5, octaves=3) - 0.5) * 0.9 * smoothstep(SQUARE_HALF + 1, SQUARE_HALF + 7, square)
height = height + (hill + yards) * inside

ponds = []  # (x, y, radius, water level)


def carve_pond(cx, cy, radius, depth):
    """Levels a basin and sinks a pond in it. Returns the water level."""
    global height
    distance = np.hypot(X - cx, Y - cy)
    base = float(height[distance < radius + 4].mean())
    basin = smoothstep(radius + 12, radius + 3, distance)
    height = height * (1 - basin) + base * basin
    height = height - depth * smoothstep(radius, radius * 0.45, distance)
    ponds.append((cx, cy, radius, base - 0.45))


def level_pad(cx, cy, radius):
    """Flattens a building plot. Returns its height."""
    global height
    distance = np.hypot(X - cx, Y - cy)
    base = float(height[distance < radius].mean())
    pad = smoothstep(radius + 10, radius, distance)
    height = height * (1 - pad) + base * pad
    return base


carve_pond(*INNER_POND)
for pond in OUTER_PONDS:
    carve_pond(*pond)
farm_level = level_pad(*FARM, 13.0)

# The hall's terrace: a stone platform cut into the top of the rise, ten steps up from the street.
HALL_GROUND = sample(height, 0.0, STAIR_FOOT_Y)
TERRACE = HALL_GROUND + TERRACE_STEPS * RISER
under_terrace = (np.abs(X) < TERRACE_HALF + 1) & (Y > TERRACE_FRONT - 1) & (Y < TERRACE_BACK + 1)
height = np.where(under_terrace, np.minimum(height, TERRACE - 0.3), height)

bank = (MOAT_OUTER - MOAT_INNER) * 0.36
moat = smoothstep(MOAT_INNER, MOAT_INNER + bank, square) * (1 - smoothstep(MOAT_OUTER - bank, MOAT_OUTER, square))
height = height - moat * MOAT_DEPTH
height = height - smoothstep(SIZE / 2 - 30, SIZE / 2 - 4, square) * 9

grad_y, grad_x = np.gradient(height, STEP)
slope = np.sqrt(grad_x**2 + grad_y**2)


def ground(x, y):
    return sample(height, x, y)


# Where grass grows: 1 everywhere to begin with, and cleared as roads,
# buildings and water are laid down. It becomes the terrain's `ground_cover`
# vertex group, which the game reads as how thickly to plant ground cover
# (src/engine/grass.ts).
cover = np.ones((count, count))


def bare_rect(lo, hi, margin=1.7):
    # The margin is nearly a whole grid step on purpose. Cover is stored per
    # terrain vertex and blends across each 2 m cell, and the game plants
    # where it passes a quarter: with a smaller margin, the blend from the
    # first grassy vertex reaches back over the edge and blades come up
    # through the paving.
    cover[(X > lo[0] - margin) & (X < hi[0] + margin) & (Y > lo[1] - margin) & (Y < hi[1] + margin)] = 0.0


def bare_along(points, radius):
    for px, py in points[::2]:
        cover[np.hypot(X - px, Y - py) < radius] = 0.0


def grows_at(x, y):
    return sample(cover, x, y) > 0.5


def lerp_color(a, b, t):
    return a + (b - a) * t[..., None]


# --- Blender helpers ---------------------------------------------------------

for datablock in list(bpy.data.objects):
    bpy.data.objects.remove(datablock)
scene = bpy.context.scene


def collection(name):
    result = bpy.data.collections.new(name)
    scene.collection.children.link(result)
    return result


def material(name, color, roughness=0.9, metallic=0.0, vertex_colors=False, texture=None, normal_map=None, glow=0.0):
    result = bpy.data.materials.new(name)
    if result.node_tree is None:
        result.use_nodes = True  # Blender < 5: node trees are opt-in
    nodes, links = result.node_tree.nodes, result.node_tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    if glow > 0:  # brighter than white: the game's bloom picks it up
        shader.inputs["Emission Color"].default_value = (*color, 1.0)
        shader.inputs["Emission Strength"].default_value = glow
    if vertex_colors:
        attribute = nodes.new("ShaderNodeVertexColor")
        attribute.layer_name = "Col"
        attribute.location = (shader.location.x - 260, shader.location.y)
        links.new(attribute.outputs["Color"], shader.inputs["Base Color"])
    if texture is not None:
        picture = nodes.new("ShaderNodeTexImage")
        picture.image = texture
        picture.location = (shader.location.x - 320, shader.location.y)
        links.new(picture.outputs["Color"], shader.inputs["Base Color"])
    if normal_map is not None:
        picture = nodes.new("ShaderNodeTexImage")
        picture.image = normal_map
        picture.location = (shader.location.x - 520, shader.location.y - 320)
        bump = nodes.new("ShaderNodeNormalMap")
        bump.location = (shader.location.x - 200, shader.location.y - 320)
        links.new(picture.outputs["Color"], bump.inputs["Color"])
        links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    return result


def add(name, mesh, where, location=(0, 0, 0), rotation_z=0.0, scale=1.0, **props):
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler = (0, 0, rotation_z)
    obj.scale = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
    where.objects.link(obj)
    for key, value in props.items():
        obj[key] = value
    return obj


UP = Vector((0.0, 0.0, 1.0))


class Frame:
    """
    A wall to build on, seen from outside: `along` runs left to right, `out`
    away from the wall, and height is the world's. Everything on a building's
    face is laid out in these three numbers, so one description of a house
    front serves whichever way the house faces.
    """

    def __init__(self, origin, along):
        self.o = (float(origin[0]), float(origin[1]))
        self.u = (float(along[0]), float(along[1]))
        self.n = (self.u[1], -self.u[0])
        self.U = Vector((self.u[0], self.u[1], 0.0))
        self.N = Vector((self.n[0], self.n[1], 0.0))

    def at(self, a, d, z):
        return Vector((self.o[0] + self.u[0] * a + self.n[0] * d, self.o[1] + self.u[1] * a + self.n[1] * d, z))

    def shifted(self, a=0.0, d=0.0):
        return Frame(self.at(a, d, 0.0)[:2], self.u)

    def right(self, a, d=0.0):
        """The wall round the corner to the right, starting at (a, d) and running back."""
        return Frame(self.at(a, d, 0.0)[:2], (-self.n[0], -self.n[1]))

    def left(self, a, d=0.0):
        """The wall round the corner to the left, starting at its far end (a, d) and running forward."""
        return Frame(self.at(a, d, 0.0)[:2], self.n)

    def reverse(self, a, d=0.0):
        """The wall facing the other way, starting at (a, d)."""
        return Frame(self.at(a, d, 0.0)[:2], (-self.u[0], -self.u[1]))

    @property
    def turn(self):
        """Rotation for a prop modelled with +X along the wall and -Y out of it."""
        return math.atan2(self.u[1], self.u[0])


def clip(outline, nx, ny, limit):
    """The part of a convex outline [(x, y), ...] where nx * x + ny * y <= limit."""
    result = []
    for i, p in enumerate(outline):
        q = outline[(i + 1) % len(outline)]
        dp, dq = nx * p[0] + ny * p[1] - limit, nx * q[0] + ny * q[1] - limit
        if dp <= 0:
            result.append(p)
        if (dp < 0 < dq) or (dq < 0 < dp):
            t = dp / (dp - dq)
            result.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return result


class Builder:
    """
    Collects boxes and other solids into one mesh with several materials.
    Faces are given UVs sized in metres as they are added, so a texture is the
    same scale everywhere.
    """

    def __init__(self, materials, tile=None):
        self.bm = bmesh.new()
        self.uv = self.bm.loops.layers.uv.verify()
        self.materials = list(materials)
        self.tile = tile

    def slot(self, mat):
        if mat not in self.materials:
            self.materials.append(mat)
        return self.materials.index(mat)

    def finish(self, faces, mat, tile=None, grain=None, origin=None):
        """
        Assigns a material and UVs to faces just created. By default the
        texture is projected along whichever axis the face looks down. With
        `grain` (a direction) the texture's U axis is laid along it instead:
        the length of a beam, the run of the eaves on a roof (so tile rows run
        along the eaves and climb the slope). `origin` is where the texture
        starts, for a full row of tiles at the eaves.
        """
        # Metres per repeat: as asked, else the texture set's real size, else 2 m.
        tile = tile or self.tile or mat.get("metres", 2.0)
        index = self.slot(mat)
        start = Vector(origin) if origin is not None else None
        for face in faces:
            face.material_index = index
            face.normal_update()
            if grain is not None:
                along = Vector(grain) - face.normal * face.normal.dot(Vector(grain))
                if along.length > 0.2:
                    along.normalize()
                    up = face.normal.cross(along)
                    for loop in face.loops:
                        p = loop.vert.co - start if start is not None else loop.vert.co
                        loop[self.uv].uv = (p.dot(along) / tile, p.dot(up) / tile)
                    continue
            facing = max(range(3), key=lambda i: abs(face.normal[i]))
            across, up = [i for i in range(3) if i != facing]
            for loop in face.loops:
                loop[self.uv].uv = (loop.vert.co[across] / tile, loop.vert.co[up] / tile)

    def poly(self, points, mat, facing=None, tile=None, grain=None, origin=None):
        """One flat face through `points`, turned to look toward `facing` if given."""
        points = [Vector(p) for p in points]
        points = [p for i, p in enumerate(points) if (p - points[i - 1]).length > 1e-5]
        if len(points) < 3:
            return None
        if facing is not None:
            normal = Vector((0.0, 0.0, 0.0))
            for i, p in enumerate(points):  # Newell's method: right for any outline, hollow corners included
                q = points[(i + 1) % len(points)]
                normal += Vector(((p.y - q.y) * (p.z + q.z), (p.z - q.z) * (p.x + q.x), (p.x - q.x) * (p.y + q.y)))
            if normal.length < 1e-9:
                return None
            if normal.dot(Vector(facing)) < 0:
                points.reverse()
        face = self.bm.faces.new([self.bm.verts.new(p) for p in points])
        self.finish([face], mat, tile, grain, origin)
        return face

    def prism(self, base, push, mat, tile=None, grain=None, front=True, back=False, sides=True):
        """
        A flat outline `base` (points in space) pushed out by the vector
        `push` into a solid: a beam, a bracket, a barge board. `sides` may be
        a list saying which edges of the outline get a side face.
        """
        base = [Vector(p) for p in base]
        push = Vector(push)
        count_ = len(base)
        flags = list(sides) if isinstance(sides, (list, tuple)) else [bool(sides)] * count_
        if front:
            self.poly([p + push for p in base], mat, push, tile, grain)
        if back:
            self.poly(base, mat, -push, tile, grain)
        normal = Vector((0.0, 0.0, 0.0))
        for i, p in enumerate(base):
            q = base[(i + 1) % count_]
            normal += Vector(((p.y - q.y) * (p.z + q.z), (p.z - q.z) * (p.x + q.x), (p.x - q.x) * (p.y + q.y)))
        for i in range(count_):
            if not flags[i]:
                continue
            p, q = base[i], base[(i + 1) % count_]
            self.poly([p, q, q + push, p + push], mat, (q - p).cross(normal), tile, grain)

    def fprism(self, F, outline, d0, d1, mat, **options):
        """A solid on a wall: the outline [(along, height), ...] standing from depth d0 out to d1."""
        self.prism([F.at(a, d0, z) for a, z in outline], F.N * (d1 - d0), mat, **options)

    def fbox(self, F, a0, a1, d0, d1, z0, z1, mat, **options):
        """A box on a wall. Its back face, against the wall, is left out."""
        if "grain" not in options and mat.get("texture_set") == "weathered_brown_planks":
            options["grain"] = F.U if (a1 - a0) >= (z1 - z0) else UP  # timber: grain along the length
        self.fprism(F, [(a0, z0), (a1, z0), (a1, z1), (a0, z1)], d0, d1, mat, **options)

    def wall(self, F, length, z0, z1, mat, openings=(), d=0.0, lining=None, a0=0.0, cuts=((), ())):
        """
        A wall face from a0 to a0 + length, with real openings: each is
        (left, right, bottom, top, depth, what closes its back or None). The
        wall is cut into a grid along every opening's edges, so neighbouring
        pieces share whole edges and no light leaks between them. An opening
        with no depth is only a hole, for the caller to fill with something
        that isn't square; `cuts` (alongs, heights) are further lines to cut
        the wall on, where such a filling has corners.
        """
        columns = sorted({a0, a0 + length} | {o[0] for o in openings} | {o[1] for o in openings} | set(cuts[0]))
        rows = sorted({z0, z1} | {o[2] for o in openings} | {o[3] for o in openings} | set(cuts[1]))
        for left, right in zip(columns[:-1], columns[1:]):
            for bottom, top in zip(rows[:-1], rows[1:]):
                ca, cz = (left + right) / 2, (bottom + top) / 2
                if any(o[0] < ca < o[1] and o[2] < cz < o[3] for o in openings):
                    continue
                self.poly([F.at(left, d, bottom), F.at(right, d, bottom), F.at(right, d, top), F.at(left, d, top)], mat, F.N)
        for opening in openings:
            if opening[4] is not None:
                self.opening(F, *opening, lining or mat, d)

    def opening(self, F, left, right, bottom, top, depth, closing, lining, d=0.0):
        """The inside of a hole in a wall: its four reveals, and what closes it at the back (glass, a door)."""
        inner = d - depth
        self.poly([F.at(left, d, bottom), F.at(left, inner, bottom), F.at(left, inner, top), F.at(left, d, top)], lining, F.U, grain=UP)
        self.poly([F.at(right, d, bottom), F.at(right, inner, bottom), F.at(right, inner, top), F.at(right, d, top)], lining, -F.U, grain=UP)
        self.poly([F.at(left, d, bottom), F.at(right, d, bottom), F.at(right, inner, bottom), F.at(left, inner, bottom)], lining, UP, grain=F.U)
        self.poly([F.at(left, d, top), F.at(right, d, top), F.at(right, inner, top), F.at(left, inner, top)], lining, -UP, grain=F.U)
        if closing is not None:
            self.poly([F.at(left, inner, bottom), F.at(right, inner, bottom), F.at(right, inner, top), F.at(left, inner, top)], closing, F.N, grain=UP)

    def box(self, lo, hi, mat, tile=None, grain=None):
        """An axis-aligned box from its low corner to its high corner. `grain="long"` runs the texture along its length."""
        before = set(self.bm.faces)
        made = bmesh.ops.create_cube(self.bm, size=1.0)
        size = [hi[i] - lo[i] for i in range(3)]
        centre = [(hi[i] + lo[i]) / 2 for i in range(3)]
        bmesh.ops.scale(self.bm, vec=size, verts=made["verts"])
        bmesh.ops.translate(self.bm, vec=centre, verts=made["verts"])
        if grain == "long":
            longest = max(range(3), key=lambda i: size[i])
            grain = [1.0 if i == longest else 0.0 for i in range(3)]
        self.finish(set(self.bm.faces) - before, mat, tile, grain)

    def cylinder(self, centre, radius, z0, z1, mat, segments=12, top_radius=None):
        before = set(self.bm.faces)
        made = bmesh.ops.create_cone(
            self.bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius if top_radius is None else top_radius, depth=z1 - z0
        )
        bmesh.ops.translate(self.bm, vec=(centre[0], centre[1], (z0 + z1) / 2), verts=made["verts"])
        self.finish(set(self.bm.faces) - before, mat)

    def saddle_run(self, start, end, mat, fall, width=0.17, height=0.07, piece=0.42, tile=None):
        """
        A row of ridge tiles from `start` to `end`: each a little saddle that
        widens and lifts toward one end and laps over the next, so the ridge
        line is stepped and not ruled. `fall` is how far the roof drops away
        over `width` on either side.
        """
        start, end = Vector(start), Vector(end)
        run = end - start
        if run.length < 0.2:
            return
        direction = run.normalized()
        side = direction.cross(UP).normalized()
        pieces = max(1, round(run.length / piece))

        def profile(point, scale, lift):
            low, high = UP * (lift - fall * scale), UP * (lift + height)
            return [point - side * width * scale + low, point - side * width * 0.42 * scale + high, point + side * width * 0.42 * scale + high, point + side * width * scale + low]

        for i in range(pieces):
            near = profile(start + run * (i / pieces), 1.0, 0.0)
            far = profile(start + run * ((i + 1) / pieces) + direction * (0.035 if i < pieces - 1 else 0.0), 1.12, 0.022)
            for k, outward in enumerate((-side + UP * 0.5, UP, side + UP * 0.5)):
                self.poly([near[k], near[k + 1], far[k + 1], far[k]], mat, outward, tile, grain=direction)
            self.poly(far, mat, direction, tile)
            if i == 0:
                self.poly(near, mat, -direction, tile)

    def roof(
        self, F, length, span, z, pitch, mat, eave=(0.45, 0.45), verge=(0.35, 0.35), hip=(0.0, 0.0), thick=0.07,
        under=None, barge=None, tails=None, ridge=None, purlins=None, tile=None, course=None, ridge_size=(0.17, 0.07, 0.42),
        tile_ends=None, verge_tiles=None,
    ):
        """
        A pitched roof with thickness. `F` runs along the top of the front
        eaves wall (its origin at that wall's left end, on the wall's face)
        and the ridge is parallel to it; the opposite wall is `span` behind.

        The roof is a slab `thick` deep whose underside rests on the wall
        tops. It overhangs the eaves walls by `eave` (front, back) and the end
        walls by `verge` (left, right), and shows what a roof shows there: the
        tile ends, the boarded underside, rafter tails and an eaves board
        under the eaves (`tails`: a material), barge boards down the gables
        (`barge`), the ends of the purlins poking through under them
        (`purlins`), and ridge tiles (`ridge`, each `ridge_size`: half width,
        height, length). `hip` (left, right) is how much of each end is
        hipped: 0 a plain gable, 1 a full hip, between them a half hip.
        `course`, for thatch, lays every slope in overlapping layers that many
        metres deep. `tile_ends`, for a roof of round tiles, models the end of
        each column of them along the eaves, so the eaves line is scalloped:
        it is (columns in one repeat of the texture, where in the repeat the
        first one's crest is), which keeps them on the texture's own columns.
        `verge_tiles` (a material) caps each gable's edge with a run of
        lapped tiles.

        Tile rows run along the eaves on every slope, hips included, and
        start with a whole row at the eaves.

        Returns (height of the ridge, a function giving the roof surface's
        height at a distance in from the front wall).
        """
        tan, half = math.tan(pitch), span / 2
        drop = thick / math.cos(pitch)  # the slab's depth measured straight down
        tile = tile or mat.get("metres", 1.5)
        under = under or mat
        if hip[0] > 0 or hip[1] > 0:
            eave = (eave[0], eave[0])  # a hip only meets both slopes cleanly if they are alike
        s0, s1 = -verge[0], length + verge[1]

        def below(c):
            return z + min(c, span - c) * tan

        def surface(c):
            return below(c) + drop

        def lay(outline, place, grain, high, outward):
            """
            Covers one slope. `outline` is [(along its eaves, distance in
            from them), ...] and `place` turns such a pair (and a lift off
            the slab) into a point. Returns the bands it was laid in.
            """
            if not course:
                self.poly([place(t, r) for t, r in outline], mat, UP, tile, grain, place(outline[0][0], 0.0))
                return []
            step = course * math.cos(pitch)
            count_ = max(1, math.ceil(high / step - 0.25))
            bands = []
            for k in range(count_):
                r0, r1 = k * step, (k + 1) * step if k < count_ - 1 else high
                band = clip(clip(outline, 0, -1, -r0), 0, 1, r1)
                if len(band) < 3:
                    continue
                lift = 0.07 if k > 0 else 0.0  # each band's foot stands off the one below
                self.poly([place(t, r, lift * (r1 - r) / (r1 - r0)) for t, r in band], mat, UP, tile, grain, place(band[0][0], r0, lift))
                if k > 0:
                    ends = sorted(t for t, r in band if abs(r - r0) < 1e-6)
                    self.poly([place(ends[0], r0), place(ends[-1], r0), place(ends[-1], r0, lift), place(ends[0], r0, lift)], mat, outward, tile, grain)
                    bands.append((r0, r1, lift))
            return bands

        crest = surface(half)
        slopes = []
        for side in (1, -1):
            out = eave[0] if side > 0 else eave[1]
            run = [hip[0] * (half + out), hip[1] * (half + out)]  # how far in along the ridge each hip reaches
            turn = [half - run[0], half - run[1]]  # where up the slope each gable gives way to its hip

            def at(s, c, lift=0.0, side=side):
                return Vector(F.at(s, -(c if side > 0 else span - c), surface(c) + lift))

            slopes.append((side, out, run, turn, at))
            outline = [(s0, -out), (s1, -out), (s1, turn[1]), (s1 - run[1], half), (s0 + run[0], half), (s0, turn[0])]
            grain = F.U * side
            start = at(s0 if side > 0 else s1, -out)
            bands = lay([(s, c + out) for s, c in outline], lambda t, r, lift=0.0, at=at, out=out: at(t, r - out, lift), grain, half + out, F.N * side)
            for r0, r1, lift in bands:  # close the verge under each lifted band
                for e, se in enumerate((s0, s1)):
                    if hip[e] < 1 and r0 - out < turn[e]:
                        self.poly([at(se, r0 - out), at(se, r0 - out, lift), at(se, min(r1 - out, turn[e]))], mat, F.U * (1 if e else -1), tile)
            self.poly([at(s, c, -drop) for s, c in outline], under, -UP, grain=F.U)
            # The eaves: tile ends.
            self.poly([at(s0, -out), at(s1, -out), at(s1, -out, -drop), at(s0, -out, -drop)], mat, F.N * side, tile, grain, start)
            if tile_ends is not None and out > 0.25:
                # Each column of tiles ends in a rounded lip that fades back up the slope.
                columns, crest_at = tile_ends
                pitch_ = tile / columns
                first = math.ceil(((0.06 if side > 0 else s0 - s1 + 0.06) / tile - crest_at) * columns)
                for k in range(first, first + int((s1 - s0 - 0.12) / pitch_)):
                    s = s0 + side * (crest_at + k / columns) * tile
                    lip = [at(s - 0.42 * pitch_, -out), at(s, -out, 0.05), at(s + 0.42 * pitch_, -out)]
                    fade = at(s, -out + 0.42, 0.003)
                    self.poly([lip[0], lip[1], fade], mat, UP - F.U, tile, grain, start)
                    self.poly([lip[1], lip[2], fade], mat, UP + F.U, tile, grain, start)
                    self.poly(lip, mat, F.N * side, tile, grain, start)
            for e, se in enumerate((s0, s1)):
                if hip[e] < 1:  # the verge: the slab's edge up the gable
                    self.poly([at(se, -out), at(se, turn[e]), at(se, turn[e], -drop), at(se, -out, -drop)], mat, F.U * (1 if e else -1), tile)
                    if verge_tiles is not None and verge[e] > 0.2:
                        inset = 0.1 * (1 if e == 0 else -1)
                        self.saddle_run(at(se + inset, turn[e] - 0.02), at(se + inset, -out + 0.03), verge_tiles, 0.004, 0.1, 0.05, 0.4, tile=tile)
                    if barge is not None and verge[e] > 0.05:
                        inward = F.U * (-1 if e else 1)
                        board = [at(se, -out + 0.02, 0.012 - drop), at(se, turn[e], 0.012 - drop), at(se, turn[e], -drop - 0.21), at(se, -out + 0.02, -drop - 0.21)]
                        self.prism([p + inward * 0.035 for p in board], inward * 0.05, barge, front=True, back=True, grain=board[1] - board[0])
            if tails is not None and out > 0.25:
                count_ = max(2, round(length / 0.62))
                tip = -out + 0.075
                for i in range(count_):
                    s = (i + 0.5) * length / count_ - 0.045
                    tail = [at(s, 0.0, -drop), at(s, tip, -drop), at(s, tip, -drop - 0.1), at(s, 0.0, -drop - 0.13)]
                    self.prism(tail, F.U * 0.09, tails, back=True, sides=[False, False, True, False], grain=tail[1] - tail[0])
                # The eaves board the tiles' ends rest on.
                e0, e1 = s0 + (0.09 if hip[0] < 1 else 0.3), s1 - (0.09 if hip[1] < 1 else 0.3)
                board = [at(e0, -out + 0.04, -drop), at(e1, -out + 0.04, -drop), at(e1, -out + 0.04, -drop - 0.11), at(e0, -out + 0.04, -drop - 0.11)]
                self.prism(board, F.N * side * -0.035, tails, back=True, grain=F.U)
            if ridge is not None and not course:  # thatch is simply dressed round its hips
                for e, se in enumerate((s0, s1)):
                    if 0 < hip[e]:
                        inward = 1 if e == 0 else -1
                        self.saddle_run(at(se + inward * run[e], half), at(se, turn[e]), ridge, ridge_size[0] * tan * 0.55, *ridge_size, tile=tile)
        side, out, run, turn, at = slopes[0]
        for e, se in enumerate((s0, s1)):
            if hip[e] > 0:
                inward = 1 if e == 0 else -1
                foot = surface(turn[e])

                def on_hip(t, r, lift=0.0, se=se, inward=inward, foot=foot):
                    return Vector(F.at(se + inward * r, -t, foot + r * tan + lift))

                corners = [(turn[e], 0.0), (span - turn[e], 0.0), (half, run[e])]
                lay(corners, on_hip, F.N * inward, run[e], F.U * -inward)
                self.poly([on_hip(t, r, -drop) for t, r in corners], under, -UP, grain=F.N)
                rim = [on_hip(turn[e], 0.0), on_hip(span - turn[e], 0.0), on_hip(span - turn[e], 0.0, -drop), on_hip(turn[e], 0.0, -drop)]
                self.poly(rim, mat, F.U * -inward, tile, F.N * inward, rim[0])
            elif purlins is not None and verge[e] > 0.2:
                outward = F.U * (-1 if e == 0 else 1)
                wall = 0.0 if e == 0 else length
                for c in (0.09, half * 0.52, half, span - half * 0.52, span - 0.09):
                    c0, c1 = c - 0.07, c + 0.07
                    low = min(below(c0), below(c1)) - 0.15
                    end = [F.at(wall, -c0, low), F.at(wall, -c1, low), F.at(wall, -c1, below(c1)), F.at(wall, -c, below(c)), F.at(wall, -c0, below(c0))]
                    self.prism(end, outward * (verge[e] - 0.05), purlins, grain=outward)
        if ridge is not None:
            start = F.at(s0 + run[0] - (0.02 if hip[0] == 0 else 0), -half, crest)
            end = F.at(s1 - run[1] + (0.02 if hip[1] == 0 else 0), -half, crest)
            self.saddle_run(start, end, ridge, ridge_size[0] * tan, *ridge_size, tile=tile)
        return crest, surface

    def pyramid_roof(self, lo, hi, z, rise, mat, overhang=0.4, tile=None):
        x0, y0, x1, y1 = lo[0] - overhang, lo[1] - overhang, hi[0] + overhang, hi[1] + overhang
        apex = ((x0 + x1) / 2, (y0 + y1) / 2, z + rise)
        corners = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
        made = [self.bm.faces.new([self.bm.verts.new(p) for p in (corners[i], corners[(i + 1) % 4], apex)]) for i in range(4)]
        made.append(self.bm.faces.new([self.bm.verts.new(p) for p in reversed(corners)]))
        self.finish(made, mat, tile)

    def stairs(self, start, direction, steps, width, mat, side=(0, 0)):
        """
        A solid flight of `steps` risers from `start` (the foot's centre, on the
        ground) climbing along `direction` (a unit X or Y vector).
        """
        dx, dy = direction
        px, py = -dy, dx  # across the flight
        for i in range(steps):
            near = (start[0] + dx * TREAD * i, start[1] + dy * TREAD * i)
            far = (start[0] + dx * TREAD * (i + 1), start[1] + dy * TREAD * (i + 1))
            xs = [near[0] - px * width / 2, near[0] + px * width / 2, far[0] - px * width / 2, far[0] + px * width / 2]
            ys = [near[1] - py * width / 2, near[1] + py * width / 2, far[1] - py * width / 2, far[1] + py * width / 2]
            self.box((min(xs), min(ys), start[2] - 1.2), (max(xs), max(ys), start[2] + RISER * (i + 1)), mat)

    def drape(self, lo, hi, mat, lift=0.05, step=1.0):
        """
        A surface laid over the ground on the rectangle lo..hi (x, y): paving
        that follows the land. Its rim turns down into the earth so no gap
        shows from the side.
        """
        xs = np.linspace(lo[0], hi[0], max(2, int(math.ceil((hi[0] - lo[0]) / step)) + 1))
        ys = np.linspace(lo[1], hi[1], max(2, int(math.ceil((hi[1] - lo[1]) / step)) + 1))
        xs = np.concatenate([[lo[0] - 0.3], xs, [hi[0] + 0.3]])
        ys = np.concatenate([[lo[1] - 0.3], ys, [hi[1] + 0.3]])
        tile = mat.get("metres", 2.0)
        index = self.slot(mat)
        rows = []
        for j, y in enumerate(ys):
            row = []
            for i, x in enumerate(xs):
                rim = i in (0, len(xs) - 1) or j in (0, len(ys) - 1)
                row.append(self.bm.verts.new((x, y, ground(float(x), float(y)) + (-0.25 if rim else lift))))
            rows.append(row)
        for j in range(len(ys) - 1):
            for i in range(len(xs) - 1):
                face = self.bm.faces.new((rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i]))
                face.material_index = index
                for loop in face.loops:
                    loop[self.uv].uv = (loop.vert.co.x / tile, loop.vert.co.y / tile)
        bare_rect(lo, hi)

    def ribbon(self, points, width, mat, lift=0.06):
        """
        A road or trail along a line of (x, y) points about a metre apart,
        draped on the ground, with a ragged edge so it doesn't look ruled.
        """
        tile = mat.get("metres", 2.0)
        index = self.slot(mat)
        travelled = 0.0
        previous_row = None
        for k, (px, py) in enumerate(points):
            ahead = points[min(k + 1, len(points) - 1)]
            behind = points[max(k - 1, 0)]
            dx, dy = ahead[0] - behind[0], ahead[1] - behind[1]
            length = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / length, dx / length
            if k > 0:
                travelled += math.hypot(px - points[k - 1][0], py - points[k - 1][1])
            half = width / 2 * (1 + 0.18 * (float(value_noise(np.array(travelled * 0.35), np.array(width))) - 0.5))
            row = []
            for across, sink in ((-half - 0.35, True), (-half, False), (-half / 2, False), (0, False), (half / 2, False), (half, False), (half + 0.35, True)):
                x, y = px + nx * across, py + ny * across
                vertex = self.bm.verts.new((x, y, ground(x, y) + (-0.2 if sink else lift)))
                row.append((vertex, (across / tile, travelled / tile)))
            if previous_row is not None:
                for a in range(len(row) - 1):
                    quad = (previous_row[a + 1], previous_row[a], row[a], row[a + 1])  # wound to face up
                    face = self.bm.faces.new([vertex for vertex, _uv in quad])
                    face.material_index = index
                    for loop, (_vertex, uv) in zip(face.loops, quad):
                        loop[self.uv].uv = uv
            previous_row = row
        bare_along(points, width / 2 + 1.2)

    def mesh(self, name, smooth=False):
        for face in self.bm.faces:
            face.smooth = smooth
        result = bpy.data.meshes.new(name)
        self.bm.to_mesh(result)
        self.bm.free()
        for mat in self.materials:
            result.materials.append(mat)
        return result


# --- Surfaces ----------------------------------------------------------------
# Real materials come from the texture sets in maps/textures/ (CC0, from Poly
# Haven: `npm run texture:get -- <id>`). Each is laid at its true size. One
# plaster set is tinted into every colour of house, and one set of planks
# serves both the pale timber and the dark beams. Without the sets the town is
# still built, in flat colours.

masonry = surface("Masonry", "medieval_blocks_03") or material("Masonry", (0.36, 0.33, 0.28), roughness=0.88)
cobbles = surface("Cobbles", "cobblestone_floor_08") or material("Cobbles", (0.28, 0.27, 0.25), roughness=0.8)
roof_tiles = surface("RoofTiles", "clay_roof_tiles_02") or material("RoofTiles", (0.30, 0.11, 0.06), roughness=0.75)
slate = surface("Slate", "roof_slates_02") or material("Slate", (0.10, 0.11, 0.13), roughness=0.6)
wood = surface("Wood", "weathered_brown_planks") or material("Wood", (0.19, 0.11, 0.055), roughness=0.85)
dark_wood = surface("DarkWood", "weathered_brown_planks", tint=(0.34, 0.29, 0.26)) or material("DarkWood", (0.07, 0.045, 0.03), roughness=0.8)
bark = surface("Bark", "bark_brown_02") or material("Bark", (0.10, 0.058, 0.03), roughness=0.95)
glass = material("WindowGlass", (0.02, 0.035, 0.05), roughness=0.08)
iron = material("Iron", (0.05, 0.05, 0.055), roughness=0.5, metallic=0.9)
lamp_glass = material("LampGlass", (1.0, 0.62, 0.25), roughness=0.3, glow=7.0)
# The game swaps its own water in for a material of this name (src/engine/water.ts).
water_surface = material("Water", (0.02, 0.07, 0.075), roughness=0.06)
PLASTERS = [
    surface(name, "white_plaster_02", tint=tint) or material(name, flat, roughness=0.95)
    for name, tint, flat in [
        ("PlasterCream", (0.93, 0.84, 0.64), (0.62, 0.55, 0.40)),
        ("PlasterRose", (0.90, 0.62, 0.55), (0.58, 0.38, 0.33)),
        ("PlasterOchre", (0.92, 0.70, 0.34), (0.60, 0.44, 0.19)),
        ("PlasterWhite", (0.96, 0.95, 0.90), (0.66, 0.65, 0.60)),
        ("PlasterSage", (0.62, 0.72, 0.55), (0.38, 0.45, 0.33)),
    ]
]
# More for the buildings: brick for chimneys and the odd ground floor, a second,
# older tiled roof gone mossy, thatch for the barn, two paints for doors and
# shutters (the planks again, recoloured), and lamplight for a few windows.
brick = surface("Brick", "medieval_red_brick") or material("Brick", (0.33, 0.12, 0.08), roughness=0.9)
roof_mossy = surface("RoofTilesMossy", "roof_tiles_14", tone=(0.115, 0.085, 0.048)) or material("RoofTilesMossy", (0.16, 0.13, 0.08), roughness=0.85)
thatch = surface("Thatch", "reed_roof_04") or material("Thatch", (0.36, 0.29, 0.16), roughness=1.0)
paint_green = surface("PaintGreen", "weathered_brown_planks", tone=(0.055, 0.10, 0.04)) or material("PaintGreen", (0.07, 0.15, 0.09), roughness=0.7)
paint_red = surface("PaintRed", "weathered_brown_planks", tone=(0.27, 0.055, 0.04)) or material("PaintRed", (0.27, 0.055, 0.04), roughness=0.7)
window_lit = material("WindowLit", (1.0, 0.5, 0.16), roughness=0.4, glow=0.9)
# Surfaces with no courses or rows to keep straight let the game shuffle their
# repeats (`detile`, src/engine/surface.ts). Masonry, roof tiles, planks
# and cobbles keep their layout and are varied in tone only.
for organic in [bark, *PLASTERS]:
    if "metres" in organic:
        organic["detile"] = 1.0

CANVAS = [
    material("CanvasRed", (0.50, 0.07, 0.05), roughness=0.9),
    material("CanvasBlue", (0.06, 0.16, 0.40), roughness=0.9),
    material("CanvasYellow", (0.62, 0.45, 0.08), roughness=0.9),
]


# --- Water and the horizon ---------------------------------------------------

world = collection("World")


def plane(name, half, z, mat, **props):
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=half)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(mat)
    return add(name, mesh, world, location=(0, 0, z), collision="none", shadows=False, **props)


# The moat's water: only seen where the ground dips below it.
plane("MoatWater", MOAT_OUTER + 2, WATER_LEVEL, water_surface, always_loaded=True)
cover[moat > 0.25] = 0.0
# Ground to the horizon, below the map's own edge, so the world doesn't end in sky.
plane("Horizon", 4000, -6.0, material("Horizon", (0.105, 0.19, 0.045), roughness=1.0), always_loaded=True)

# Ponds: a disc of water in each hollow. Being named "Water", they can be swum in.
for number, (cx, cy, radius, level) in enumerate(ponds):
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, segments=28, radius=radius)
    mesh = bpy.data.meshes.new(f"Pond{number}")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(water_surface)
    add(f"Pond{number}", mesh, world, location=(cx, cy, level), collision="none", shadows=False)
    cover[np.hypot(X - cx, Y - cy) < radius * 0.9] = 0.0


# --- Paving ------------------------------------------------------------------
# Cobbles laid over the ground, following it up the hill to the hall.

town = collection("Town")
PAVING_LIFT = 0.05

paving = Builder([cobbles])
paving.drape((-SQUARE_HALF, -SQUARE_HALF), (SQUARE_HALF, SQUARE_HALF), cobbles)  # market square
paving.drape((-STREET_HALF, -WALL + 1.2), (STREET_HALF, -SQUARE_HALF), cobbles)  # gate to square
paving.drape((-STREET_HALF, SQUARE_HALF), (STREET_HALF, STAIR_FOOT_Y), cobbles)  # square up to the hall steps
paving.drape((-WALL + 6, -LANE_HALF), (-SQUARE_HALF, LANE_HALF), cobbles)  # west lane
paving.drape((SQUARE_HALF, -LANE_HALF), (WALL - 6, LANE_HALF), cobbles)  # east lane
add("Paving", paving.mesh("Paving", smooth=True), town, lod_ratio=1.0, shadows=False)

# The road out of town and the trails off it: bare earth worn into the grass.
dirt = surface("Dirt", "dirt_floor") or material("Dirt", (0.20, 0.15, 0.09), roughness=1.0)


def polyline(waypoints, spacing=1.0):
    """Points every `spacing` metres along a path through the waypoints, with the corners rounded off."""
    points = [np.array(w, dtype=float) for w in waypoints]
    for _ in range(3):  # corner cutting
        cut = [points[0]]
        for a, b in zip(points[:-1], points[1:]):
            cut += [a * 0.75 + b * 0.25, a * 0.25 + b * 0.75]
        points = cut + [points[-1]]
    result, carry = [tuple(points[0])], 0.0
    for a, b in zip(points[:-1], points[1:]):
        length = float(np.linalg.norm(b - a))
        position = spacing - carry
        while position <= length:
            result.append(tuple(a + (b - a) * position / length))
            position += spacing
        carry = (carry + length) % spacing
    return result


road_points = [(float(road_x(y)), float(y)) for y in np.arange(-MOAT_OUTER - 2.0, -SIZE / 2 + 6, -1.0)]
junction = (float(road_x(-104.0)), -104.0)
pond_x, pond_y, pond_radius, _level = ponds[1]
# To the pond, round its north shore, and up to the highest ground beyond it.
beyond = (X > 60) & (X < 150) & (Y > -175) & (Y < -70) & (np.hypot(X - pond_x, Y - pond_y) > pond_radius + 25)
summit = np.unravel_index(np.argmax(np.where(beyond, height, -1e9)), height.shape)
VIEWPOINT = (float(X[summit]), float(Y[summit]))
pond_trail = polyline(
    [
        junction,
        (junction[0] + 22, -100.0),
        (pond_x - pond_radius - 7, pond_y + 6),
        (pond_x - 4, pond_y + pond_radius + 5),
        (pond_x + pond_radius + 6, pond_y + 5),
        ((pond_x + pond_radius + 6 + VIEWPOINT[0]) / 2 + 8, (pond_y + VIEWPOINT[1]) / 2),
        VIEWPOINT,
    ]
)
farm_fork = (float(road_x(-112.0)), -112.0)
farm_trail = polyline([farm_fork, (farm_fork[0] - 14, -113.0), (FARM[0] + 9, FARM[1] + 3)])



def wear(points, half, depth):
    """Feet and wheels wear a way down into the ground: sinks the land along a path, deepest in the middle."""
    global height
    nearest = np.full(height.shape, 1e9)
    for px, py in points:
        nearest = np.minimum(nearest, np.hypot(X - px, Y - py))
    worn = smoothstep(half + 1.8, half * 0.4, nearest) * smoothstep(-MOAT_OUTER - 3, -MOAT_OUTER - 10, Y)  # not at the bridge's foot
    height = height - depth * worn


wear(road_points, 2.5, 0.2)
wear(pond_trail, 1.0, 0.15)
wear(farm_trail, 1.0, 0.15)

# The paths are not objects of their own: they are painted into the ground. A
# mask image over the whole terrain is white where earth shows through the
# grass, and the game blends the two materials along it (see the terrain
# object at the end, and `blend_layer` in export_map.py). So paths can cross
# and fork without overlapping, and their edges break up into the grass.
MASK_SIZE = 1024
mask_axis = (np.arange(MASK_SIZE) + 0.5) / MASK_SIZE * SIZE - SIZE / 2
mask_x, mask_y = np.meshgrid(mask_axis, mask_axis, indexing="xy")
path_mask = np.zeros((MASK_SIZE, MASK_SIZE))
paths = []  # (points, half width), also used to keep grass and trees off them


def paint_path(points, width):
    """Paints a path into the mask: solid for most of its width, fading over the last stretch."""
    global path_mask
    half = width / 2
    paths.append((points, half))
    xs, ys = np.array([p[0] for p in points]), np.array([p[1] for p in points])
    # Only the pixels near the path need looking at.
    columns = np.where((mask_axis > xs.min() - half - 2) & (mask_axis < xs.max() + half + 2))[0]
    rows = np.where((mask_axis > ys.min() - half - 2) & (mask_axis < ys.max() + half + 2))[0]
    if len(columns) == 0 or len(rows) == 0:
        return
    gx, gy = mask_x[np.ix_(rows, columns)], mask_y[np.ix_(rows, columns)]
    nearest = np.full(gx.shape, 1e9)
    for px, py in points:
        nearest = np.minimum(nearest, np.hypot(gx - px, gy - py))
    worn = smoothstep(half + 0.5, half - 0.7, nearest)
    path_mask[np.ix_(rows, columns)] = np.maximum(path_mask[np.ix_(rows, columns)], worn)
    bare_along(points, half + 0.9)


paint_path(road_points, 5.0)
paint_path(pond_trail, 1.7)
paint_path(farm_trail, 1.7)
# Bare earth where feet gather: round the farm, and at the foot of the bridge.
for cx, cy, radius in [(FARM[0] + 3, FARM[1] - 1, 7.0), (0.0, -MOAT_OUTER - 4.0, 4.5)]:
    path_mask = np.maximum(path_mask, 0.9 * smoothstep(radius, radius * 0.5, np.hypot(mask_x - cx, mask_y - cy)))


# --- Town wall ---------------------------------------------------------------
# A curtain wall with a walk along the top, towers at the corners and at
# intervals, and a gatehouse. What makes it read as a wall and not a textured
# box, in the order it matters:
#
# - A walling stone that does not visibly repeat. The rubble is one scan 12 m
#   across (`defense_wall_02`), where the cut stone used elsewhere repeats
#   every 2 m: on a 100 m wall that was fifty copies side by side.
# - Shape: a sloped foot, a string course on corbels under the parapet,
#   merlons with caps, towers that stand forward of the wall every 18 m or so.
#   Each casts a shadow line, and the towers break the long faces into bays.
# - Two kinds of stone: rubble for the walling, cut stone for everything that
#   would have been cut (corners, caps, sills, the arch).
# - Weather, painted into vertex colours: damp and green at the foot, stains
#   running down from the top, patches lighter and darker. The faces are laid
#   in metre-wide strips so there are vertices to paint.
#
# The wall is two objects. `TownWall` is what you can stand on or walk into.
# `TownWallTrim` is the small stonework (corbels, corner stones, caps, arrow
# slits), which has no collision: a few thousand triangles nobody can touch
# have no business in a collision shape.

wall_stone = surface("WallStone", "defense_wall_02", vertex_colors="Col", even=0.8) or material("WallStone", (0.42, 0.37, 0.29), roughness=0.92, vertex_colors=True)
wall_stone["variation"] = 0.6
slit_dark = material("ArrowSlit", (0.01, 0.01, 0.012), roughness=1.0)

walls = Builder([wall_stone, masonry])
wall_trim = Builder([masonry, slit_dark])
half = WALL_THICK / 2
TOWER_HALF, TOWER_HEIGHT = 3.6, 8.4
PARAPET = 0.95
PARAPET_THICK = 0.45
MERLON, CRENEL, MERLON_HIGH = 1.25, 0.8, 0.85
BATTER_OUT, BATTER_TOP = 0.5, 1.6  # the sloped foot: how far out it starts, and where it meets the upright face
BAND, BAND_OUT = 0.28, 0.2  # the string course under the parapet: its height and how far it stands out
WALL_FOOT = -1.2  # walls start below the ground, so no slope leaves a gap under them


def block(builder, p, q, mat):
    """A box between two opposite corners, given in any order."""
    builder.box(tuple(min(p[i], q[i]) for i in range(3)), tuple(max(p[i], q[i]) for i in range(3)), mat)


def wall_face(F, a0, a1, profile, step=1.05, mat=None, foot=None):
    """
    A stretch of wall face from a0 to a1 along the frame. `profile` is its
    section from foot to top, [(out, height), ...]; it is laid in upright
    strips about `step` wide. `foot(a)`, if given, replaces the height of the
    lowest point (the underside of an arch).
    """
    strips = max(1, round((a1 - a0) / step))
    for i in range(strips):
        p, q = a0 + (a1 - a0) * i / strips, a0 + (a1 - a0) * (i + 1) / strips
        for row, ((d0, z0), (d1, z1)) in enumerate(zip(profile, profile[1:])):
            zp, zq = (foot(p), foot(q)) if foot is not None and row == 0 else (z0, z0)
            walls.poly([F.at(p, d0, zp), F.at(q, d0, zq), F.at(q, d1, z1), F.at(p, d1, z1)], mat or wall_stone, F.N)


def string_course(F, a0, a1, top, corbels=True):
    """The projecting band a parapet stands on, with the row of corbels that carries it."""
    block(walls, F.at(a0, 0.0, top - BAND), F.at(a1, BAND_OUT, top), masonry)
    if corbels:
        number = max(1, round((a1 - a0) / 0.8))
        for i in range(number):
            c = a0 + (a1 - a0) * (i + 0.5) / number
            section = [F.at(c - 0.13, 0.0, top - BAND - 0.42), F.at(c - 0.13, 0.0, top - BAND), F.at(c - 0.13, BAND_OUT - 0.02, top - BAND), F.at(c - 0.13, BAND_OUT - 0.02, top - BAND - 0.13)]
            wall_trim.prism(section, F.U * 0.26, masonry, back=True)


def parapet(F, a0, a1, top):
    """A crenellated parapet along the outer edge: a low wall, merlons with caps, the first and last at the ends."""
    spans = max(1, round((a1 - a0 - MERLON) / (MERLON + CRENEL)))
    pitch = (a1 - a0 - MERLON) / spans
    inner, outer = BAND_OUT - PARAPET_THICK, BAND_OUT
    for i in range(spans + 1):
        m = a0 + i * pitch
        block(walls, F.at(m, inner, top), F.at(min(m + pitch, a1), outer, top + PARAPET), wall_stone)
        block(walls, F.at(m, inner, top + PARAPET), F.at(m + MERLON, outer, top + PARAPET + MERLON_HIGH), wall_stone)
        block(wall_trim, F.at(m - 0.04, inner - 0.04, top + PARAPET + MERLON_HIGH), F.at(m + MERLON + 0.04, outer + 0.04, top + PARAPET + MERLON_HIGH + 0.09), masonry)
        if i < spans:  # the sill of the gap, in cut stone
            block(wall_trim, F.at(m + MERLON, inner - 0.03, top + PARAPET), F.at(m + pitch, outer + 0.03, top + PARAPET + 0.06), masonry)


def arrow_slit(F, a, z, tall=1.3):
    """A slit window: a dark slot between cut-stone jambs, sill and lintel."""
    block(wall_trim, F.at(a - 0.07, 0.0, z), F.at(a + 0.07, 0.014, z + tall), slit_dark)
    for side in (-1, 1):
        block(wall_trim, F.at(a + side * 0.07, 0.0, z - 0.02), F.at(a + side * 0.27, 0.03, z + tall + 0.02), masonry)
    block(wall_trim, F.at(a - 0.3, 0.0, z + tall), F.at(a + 0.3, 0.035, z + tall + 0.2), masonry)
    block(wall_trim, F.at(a - 0.3, 0.0, z - 0.16), F.at(a + 0.3, 0.05, z), masonry)


def tower(x0, y0, x1, y1, top, slits="", parapets="senw"):
    """
    A square tower between two corners: upright faces on a cut-stone plinth,
    corner stones, a corbelled string course and a crenellated parapet.
    `slits` and `parapets` name sides by compass letter.
    """
    sides = {
        "s": (Frame((x0, y0), (1, 0)), x1 - x0),
        "e": (Frame((x1, y0), (0, 1)), y1 - y0),
        "n": (Frame((x1, y1), (-1, 0)), x1 - x0),
        "w": (Frame((x0, y1), (0, -1)), y1 - y0),
    }
    rows = [WALL_FOOT, 0.9, 2.8] + [z for z in (4.8, 6.6) if z < top - BAND - 0.8] + [top - BAND]
    for key, (G, length) in sides.items():
        wall_face(G, 0.0, length, [(0.0, z) for z in rows], step=1.2)
        block(walls, G.at(-0.14, 0.0, WALL_FOOT), G.at(length + 0.14, 0.14, 0.55), masonry)  # plinth
        string_course(G, -BAND_OUT, length + BAND_OUT, top)
        if key in parapets:
            parapet(G, -BAND_OUT, length + BAND_OUT, top)
        if key in slits:
            for z in [2.5] + ([5.4] if top > 7.5 else []):
                arrow_slit(G, length / 2, z)
        # Corner stones at this side's left corner, long and short by turns round the corner.
        z, course = 0.55, 0
        while z + 0.36 < top - BAND - 0.05:
            along, round_corner = (0.62, 0.34) if course % 2 == 0 else (0.34, 0.62)
            block(wall_trim, G.at(-0.035, 0.035, z), G.at(along, -round_corner, z + 0.36), masonry)
            z += 0.42
            course += 1
    walls.poly([(x0, y0, top), (x1, y0, top), (x1, y1, top), (x0, y1, top)], masonry, UP)


def curtain(F, length, top=WALL_HEIGHT, towers=0):
    """
    A run of curtain wall, its outer face on the frame: sloped foot, string
    course, parapet, the walk behind it and the plain inner face. `towers`
    stand forward of it at even intervals.
    """
    wall_face(F, 0.0, length, [(BATTER_OUT, WALL_FOOT), (0.0, BATTER_TOP), (0.0, 3.3), (0.0, top - BAND)])
    string_course(F, 0.0, length, top)
    parapet(F, 0.0, length, top)
    wall_face(F.reverse(length, -WALL_THICK), 0.0, length, [(0.0, WALL_FOOT), (0.0, 2.6), (0.0, top)], step=2.1)
    strips = max(1, round(length / 2.1))
    for i in range(strips):
        p, q = length * i / strips, length * (i + 1) / strips
        walls.poly([F.at(p, BAND_OUT - PARAPET_THICK, top), F.at(q, BAND_OUT - PARAPET_THICK, top), F.at(q, -WALL_THICK, top), F.at(p, -WALL_THICK, top)], masonry, UP)
    facing = {(0, -1): "s", (0, 1): "n", (-1, 0): "w", (1, 0): "e"}[(round(F.n[0]), round(F.n[1]))]
    back = {"s": "n", "n": "s", "w": "e", "e": "w"}[facing]
    for i in range(towers):
        c = length * (i + 1) / (towers + 1)
        p, q = F.at(c - 2.2, -0.75, 0.0), F.at(c + 2.2, 1.4, 0.0)
        tower(min(p.x, q.x), min(p.y, q.y), max(p.x, q.x), max(p.y, q.y), top + 1.5, slits=facing, parapets="senw".replace(back, ""))


corner = WALL - TOWER_HALF
GATE_TOWER = 2.6  # half-width of the towers either side of the gate
gate_tower_x = GATE_HALF + GATE_TOWER
gate_side = corner - (gate_tower_x + GATE_TOWER)  # length of the south wall either side of the gatehouse
curtain(Frame((-corner, -WALL - half), (1, 0)), gate_side, towers=1)  # south, west of the gate
curtain(Frame((gate_tower_x + GATE_TOWER, -WALL - half), (1, 0)), gate_side, towers=1)  # south, east of the gate
curtain(Frame((corner, WALL + half), (-1, 0)), 2 * corner, towers=4)  # north
curtain(Frame((-WALL - half, corner), (0, -1)), 2 * corner, towers=4)  # west
curtain(Frame((WALL + half, -corner), (0, 1)), 2 * corner, towers=4)  # east
for sx in (-1, 1):
    for sy in (-1, 1):
        cx, cy = sx * WALL, sy * WALL
        outward = ("w" if sx < 0 else "e") + ("s" if sy < 0 else "n")
        tower(cx - TOWER_HALF, cy - TOWER_HALF, cx + TOWER_HALF, cy + TOWER_HALF, TOWER_HEIGHT, slits=outward)
    # Gatehouse: a tower each side of the opening, a little taller than the span between them.
    gx = sx * gate_tower_x
    tower(gx - GATE_TOWER, -WALL - 2.2, gx + GATE_TOWER, -WALL + 2.2, TOWER_HEIGHT + 1.2, slits="s")

# The span over the gate. It is hollow, which is where the raised portcullis
# (built by the game: see the "portcullis" marker) goes. The opening is arched:
# a segment of a circle springing 3 m up the towers and 4.4 m high at the crown.
ARCH_SPRING, ARCH_CROWN = 3.0, 4.4
arch_radius = (GATE_HALF**2 + (ARCH_CROWN - ARCH_SPRING) ** 2) / (2 * (ARCH_CROWN - ARCH_SPRING))


def arch(a):
    """Height of the gate arch's underside, `a` metres from its left springing."""
    return ARCH_CROWN - arch_radius + math.sqrt(max(arch_radius**2 - (a - GATE_HALF) ** 2, 0.0))


gate_faces = [Frame((-GATE_HALF, -WALL - 1.6), (1, 0)), Frame((GATE_HALF, -WALL + 1.6), (-1, 0))]
for G in gate_faces:
    wall_face(G, 0.0, 2 * GATE_HALF, [(0.0, ARCH_SPRING), (0.0, 6.2), (0.0, TOWER_HEIGHT - BAND)], step=0.5, foot=arch)
    string_course(G, 0.0, 2 * GATE_HALF, TOWER_HEIGHT)
    parapet(G, 0.0, 2 * GATE_HALF, TOWER_HEIGHT)
    # The ring of cut stones round the arch.
    STONES = 15
    for k in range(STONES):
        ring = []
        for radius, t in ((arch_radius, k), (arch_radius, k + 1), (arch_radius + 0.45, k + 1), (arch_radius + 0.45, k)):
            angle = math.asin(GATE_HALF / arch_radius) * (2 * t / STONES - 1)
            ring.append(G.at(GATE_HALF + radius * math.sin(angle), 0.0, ARCH_CROWN - arch_radius + radius * math.cos(angle)))
        wall_trim.prism(ring, G.N * (0.11 if k != STONES // 2 else 0.17), masonry)
south, north = gate_faces
STRIPS = 12
for i in range(STRIPS):  # the underside of the arch, right through the gatehouse
    p, q = 2 * GATE_HALF * i / STRIPS, 2 * GATE_HALF * (i + 1) / STRIPS
    walls.poly([south.at(p, 0.0, arch(p)), south.at(q, 0.0, arch(q)), south.at(q, -3.2, arch(q)), south.at(p, -3.2, arch(p))], masonry, -UP)
walls.poly([south.at(0.0, 0.0, TOWER_HEIGHT), south.at(2 * GATE_HALF, 0.0, TOWER_HEIGHT), south.at(2 * GATE_HALF, -3.2, TOWER_HEIGHT), south.at(0.0, -3.2, TOWER_HEIGHT)], masonry, UP)

# Stairs to the wall walk, inside the south wall, climbing west from near the gate.
stair_y = -WALL + half + 0.8
stair_foot_x = -(gate_tower_x + GATE_TOWER) - 1.0
walls.stairs((stair_foot_x, stair_y, 0.0), (-1, 0), WALL_STEPS, 1.6, masonry)
stair_top_x = stair_foot_x - WALL_STEPS * TREAD
# A landing joining the top step to the wall walk.
walls.box((stair_top_x - 2.0, -WALL + half, -0.3), (stair_top_x, stair_y + 0.8, WALL_HEIGHT), masonry)


def weather(mesh):
    """
    Paints a wall's weathering into its vertex colours (which its walling
    stone is multiplied by): patches lighter and darker, damp and green toward
    the foot, dark runs down from the top, a little bleaching high up.
    """
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    along, z = co[:, 0] + co[:, 1], co[:, 2]  # every face runs along X or Y, so their sum runs along it too
    patches = fbm(along + 900.0, z * 2.5 + 300.0, octaves=3, frequency=1 / 9)
    damp = (1 - smoothstep(0.1, 2.0, z)) * (0.55 + 0.45 * value_noise(along * 0.21 + 40.0, z * 0.5 + 9.0))
    runs = np.clip((value_noise(along * 0.93 + 31.7, np.full_like(along, 3.3)) - 0.5) / 0.3, 0.0, 1.0) * smoothstep(1.2, 4.6, z)
    light = (0.8 + 0.34 * patches) * (1 - 0.34 * damp) * (1 - 0.26 * runs) * (1 + 0.07 * smoothstep(4.5, 7.5, z))
    stain = np.clip(0.75 * damp + 0.3 * runs, 0.0, 1.0)[:, None]
    tint = np.array([0.93, 0.93, 0.95]) * (1 - stain) + np.array([0.70, 0.84, 0.62]) * stain
    rgba = np.concatenate([np.clip(light[:, None] * tint, 0.0, 1.0), np.ones((len(co), 1))], axis=1)
    paint = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    paint.data.foreach_set("color", rgba.astype(np.float32).ravel())
    mesh.color_attributes.active_color = paint


wall_mesh = walls.mesh("TownWall")
weather(wall_mesh)
add("TownWall", wall_mesh, town, lod_ratio=1.0)
add("TownWallTrim", wall_trim.mesh("TownWallTrim"), town, collision="none", shadows=False, lod_skip=True)


# --- Bridge over the moat ----------------------------------------------------

bridge = Builder([masonry])
BRIDGE_HALF, DECK_TOP = 3.4, 0.1
bridge.box((-BRIDGE_HALF, -MOAT_OUTER - 2.5, -0.5), (BRIDGE_HALF, -MOAT_INNER + 2.5, DECK_TOP), masonry)
for side in (-1, 1):
    edge = side * (BRIDGE_HALF - 0.2)
    bridge.box((edge - 0.2, -MOAT_OUTER - 2.5, DECK_TOP), (edge + 0.2, -MOAT_INNER + 2.5, DECK_TOP + 0.7), masonry)
for pier_y in (-MOAT_INNER - 4.0, -MOAT_OUTER + 4.0):
    bridge.box((-BRIDGE_HALF + 0.3, pier_y - 0.8, -MOAT_DEPTH - 1.0), (BRIDGE_HALF - 0.3, pier_y + 0.8, -0.5), masonry)
add("Bridge", bridge.mesh("Bridge"), town, lod_ratio=1.0)


# --- Houses ------------------------------------------------------------------
# Timber-framed: a stone footing with a weathered plinth, storeys of plaster
# (or brick) between dark beams, the upper ones often jettied out over the
# street, and a roof with real edges. A whole block of them goes into one
# mesh. What is the same on every house -- a window's frame, a shutter, a door,
# a chimney pot -- is not in that mesh: the house only records where each goes
# (`dress`), and they are placed later as linked duplicates of one mesh each
# (see "Dressing the houses").

chimney_tops = []
dressing = []  # (kind, x, y, z, turn, options): where the houses want their fittings
WINDOWS = {"A": (0.9, 1.2), "B": (1.5, 1.2), "C": (0.6, 0.8), "S": (2.0, 1.3)}  # width and height of each kind of window
RECESS = 0.14  # how far the glass sits behind the wall's face
PROUD = 0.045  # how far the timbers stand out from the plaster
SILL = 1.0  # window sills above the floor
INFILLS = ["square", "close", "saltire", "chevron", "brace"]
CLAY_COLUMNS = (23, 0.016)  # the clay tile texture: columns of tiles in one repeat, and where in it the first one's crest lies


def dress(kind, F, a, z, d=0.0, **options):
    x, y, _z = F.at(a, d, z)
    dressing.append((kind, x, y, z, F.turn, options))


def window(F, kind, centre, z, openings, lit=False):
    """An opening for a window of this kind, added to the list for `Builder.wall`, and its frame among the dressing."""
    wide, tall = WINDOWS[kind]
    openings.append((centre - wide / 2, centre + wide / 2, z, z + tall, RECESS, glass))
    dress("Window" + kind, F, centre, z)
    if lit:
        dress("Lit" + kind, F, centre, z)


def sorted_box(p, q):
    """Two opposite corners in any order -> (low corner, high corner), for `Builder.box`."""
    return tuple(min(p[i], q[i]) for i in range(3)), tuple(max(p[i], q[i]) for i in range(3))


def timber_wall(b, F, length, z0, z1, openings, posts, infill, plate=0.18, rail=0.2):
    """
    The timbers of one storey of one wall: a rail along the foot, a plate
    along the top, posts, and between them the pattern that tells one house
    from the next. Members stop at openings. Lesser members stand out a
    little less than the ones they run into, so that where two meet or cross
    neither flickers.
    """

    def member(a0, a1, zb, zt, depth):
        upright = (zt - zb) > (a1 - a0)
        if upright:
            cuts = sorted((o[2], o[3]) for o in openings if o[0] < a1 and o[1] > a0)
            lo, hi = zb, zt
        else:
            cuts = sorted((o[0], o[1]) for o in openings if o[2] < zt and o[3] > zb)
            lo, hi = a0, a1
        spans, at = [], lo
        for c0, c1 in cuts:
            if c0 > at:
                spans.append((at, min(c0, hi)))
            at = max(at, c1)
        if at < hi:
            spans.append((at, hi))
        for s0, s1 in spans:
            if s1 - s0 < 0.05:
                continue
            # A member's ends butt against others or run in under a window's surround, so only its face and long sides are made.
            if upright:
                b.fbox(F, a0, a1, 0.0, depth, s0, s1, dark_wood, sides=[False, True, False, True])
            else:
                b.fbox(F, s0, s1, 0.0, depth, zb, zt, dark_wood, sides=[True, False, True, False])

    def blocked(a0, a1, zb, zt):
        return any(o[0] < a1 and o[1] > a0 and o[2] < zt and o[3] > zb for o in openings)

    def diagonal(a_foot, a_head, zb, zt, wide=0.13, depth=PROUD - 0.012):
        if blocked(min(a_foot, a_head) - 0.05, max(a_foot, a_head) + 0.05, zb, zt):
            return
        run = math.hypot(a_head - a_foot, zt - zb)
        w = wide / 2 * run / (zt - zb)  # half its width, measured along the wall
        outline = [(a_foot - w, zb), (a_foot + w, zb), (a_head + w, zt), (a_head - w, zt)]
        b.fprism(F, outline, 0.0, depth, dark_wood, grain=F.U * (a_head - a_foot) + UP * (zt - zb))

    member(0.0, length, z0, z0 + rail, PROUD)
    if plate:
        member(0.0, length, z1 - plate, z1, PROUD)
    low, high = z0 + rail, z1 - plate
    under, over = z0 + SILL - 0.13, z0 + SILL + 1.2  # the rails under and over the windows
    for a in posts:
        member(a - 0.09, a + 0.09, low, high, PROUD)
    if infill == "posts":  # a wall against a neighbour: only what shows above a lower roof
        return
    edges = [0.18] + list(posts) + [length - 0.18]
    for i, (p0, p1) in enumerate(zip(edges[:-1], edges[1:])):
        p0, p1 = p0 + (0.09 if i > 0 else 0.0), p1 - (0.09 if i < len(edges) - 2 else 0.0)
        wide, mid = p1 - p0, (p0 + p1) / 2
        if wide < 0.5:
            continue
        member(p0, p1, under, under + 0.13, PROUD - 0.004)
        if infill != "close":
            member(p0, p1, over, over + 0.12, PROUD - 0.004)
        if infill == "close":
            studs = max(2, round(wide / 0.5))
            for k in range(1, studs):
                a = p0 + wide * k / studs
                member(a - 0.055, a + 0.055, low, under, PROUD - 0.008)
                member(a - 0.055, a + 0.055, under + 0.13, high, PROUD - 0.008)
        elif infill == "saltire":
            crosses = 2 if wide > 1.7 else 1
            for k in range(crosses):
                c0, c1 = p0 + wide * k / crosses + 0.1, p0 + wide * (k + 1) / crosses - 0.1
                diagonal(c0, c1, low, under)
                diagonal(c1, c0, low, under, depth=PROUD - 0.02)
            member(mid - 0.055, mid + 0.055, over + 0.12, high, PROUD - 0.008)
        elif infill == "chevron":
            diagonal(p0 + 0.12, mid - 0.1, low, under)
            diagonal(p1 - 0.12, mid + 0.1, low, under)
            for a in (p0 + wide * 0.25, p0 + wide * 0.75):
                member(a - 0.055, a + 0.055, over + 0.12, high, PROUD - 0.008)
        elif infill == "brace":
            if i == 0:
                diagonal(p0 + 0.1, p0 + min(1.1, wide * 0.5), low, under)
            elif i == len(edges) - 2:
                diagonal(p1 - 0.1, p1 - min(1.1, wide * 0.5), low, under)
            else:
                member(mid - 0.055, mid + 0.055, low, under, PROUD - 0.008)
            for a in (p0 + wide * 0.22, p0 + wide * 0.78):
                member(a - 0.055, a + 0.055, under + 0.13, over, PROUD - 0.008)
        else:  # square panels
            member(mid - 0.055, mid + 0.055, low, under, PROUD - 0.008)
            member(mid - 0.055, mid + 0.055, under + 0.13, over, PROUD - 0.008)
            member(mid - 0.055, mid + 0.055, over + 0.12, high, PROUD - 0.008)


def masonry_wall(b, F, length, openings, mat):
    """What a stone or brick storey shows: a lintel over each opening, of timber in stone and of stone in brick."""
    lintel = dark_wood if mat is masonry else masonry
    for left, right, _bottom, top, _depth, closing in openings:
        if closing is glass:  # a door has its own frame
            b.fbox(F, max(left - 0.2, 0.0), min(right + 0.2, length), 0.0, 0.03, top, top + 0.22, lintel)


def gable_end(b, G, span, z, pitch, mat, infill="king", cap=None, light=None):
    """
    The triangle of wall under the end of a roof, with its timbers. `G` runs
    along its foot. `cap` cuts it off level where a half hip comes down over
    it; `light` is a kind of window to put in it.
    """
    tan, half = math.tan(pitch), span / 2
    apex = z + half * tan
    top = min(apex, cap) if cap is not None else apex

    def inside(outline):
        outline = clip(clip(outline, -tan, 1, z), tan, 1, z + span * tan)
        return clip(outline, 0, 1, top) if top < apex else outline

    columns, rows, hole = [0.0, span], [z, top], None
    if light is not None and top - z > WINDOWS[light][1] + 0.75:
        wide, tall = WINDOWS[light]
        bottom = z + 0.42
        if (bottom + tall + 0.25 - z) / tan < half - wide / 2 - 0.2:  # room for it under the rafters
            hole = (half - wide / 2, half + wide / 2, bottom, bottom + tall)
            columns += [hole[0], hole[1]]
            rows += [hole[2], hole[3]]
            dress("Window" + light, G, half, bottom)
            b.opening(G, *hole, RECESS, glass, dark_wood)
    columns, rows = sorted(columns), sorted(rows)
    for left, right in zip(columns[:-1], columns[1:]):
        for bottom, upper in zip(rows[:-1], rows[1:]):
            if hole and hole[0] <= (left + right) / 2 <= hole[1] and hole[2] <= (bottom + upper) / 2 <= hole[3]:
                continue
            piece = inside([(left, bottom), (right, bottom), (right, upper), (left, upper)])
            if len(piece) >= 3:
                b.poly([G.at(a, 0.0, h) for a, h in piece], mat, G.N)

    def member(outline, depth, grain):
        """A timber: its outline cut to the gable, and in two where the window is."""
        parts = [inside(outline)]
        if hole and min(a for a, _h in outline) < hole[1] and max(a for a, _h in outline) > hole[0]:
            parts = [clip(parts[0], 0, 1, hole[2] - 0.02), clip(parts[0], 0, -1, -hole[3] - 0.02)]
        for part in parts:
            if len(part) >= 3 and max(h for _a, h in part) - min(h for _a, h in part) > 0.04:
                b.fprism(G, part, 0.0, depth, dark_wood, grain=grain)

    deep = 0.24  # the rafters seen in the gable, measured straight down
    member([(0.0, z), (half, apex), (half, apex - deep), (deep / tan, z)], PROUD + 0.006, G.U + UP * tan)
    member([(span, z), (span - deep / tan, z), (half, apex - deep), (half, apex)], PROUD + 0.006, -G.U + UP * tan)
    if infill == "plain":
        return
    collar = z + (apex - z) * 0.5
    member([(0.0, collar), (span, collar), (span, collar + 0.14), (0.0, collar + 0.14)], PROUD - 0.002, G.U)
    if infill == "studs":
        studs = max(2, round(span / 0.62))
        for k in range(1, studs):
            a = span * k / studs
            member([(a - 0.055, z), (a + 0.055, z), (a + 0.055, apex), (a - 0.055, apex)], PROUD - 0.008, UP)
    elif infill == "struts":
        for side in (-1, 1):
            foot, head = half + side * 0.35, half + side * (half - (collar - z) / tan - 0.05)
            member([(foot - 0.09, z), (foot + 0.09, z), (head + 0.09, collar), (head - 0.09, collar)], PROUD - 0.012, G.U * (head - foot) + UP * (collar - z))
        member([(half - 0.07, collar), (half + 0.07, collar), (half + 0.07, apex), (half - 0.07, apex)], PROUD - 0.008, UP)
    else:  # a king post, with a stud either side below the collar
        member([(half - 0.08, z), (half + 0.08, z), (half + 0.08, apex), (half - 0.08, apex)], PROUD - 0.008, UP)
        for side in (-1, 1):
            a = half + side * half * 0.5
            member([(a - 0.055, z), (a + 0.055, z), (a + 0.055, collar), (a - 0.055, collar)], PROUD - 0.008, UP)


def chimney_stack(b, F, a, d, z0, z1, kind):
    """A chimney standing at (a, d) on the frame, from z0 (hidden in the roof) to z1. Records where its smoke comes out."""
    if kind == "brick":
        wa, wd = 0.5, 0.32
        b.fbox(F, a - wa, a + wa, d - wd, d + wd, z0, z1 - 0.3, brick, back=True)
        # Oversailing courses, each a little wider than the last, then a stone cap.
        b.fbox(F, a - wa - 0.04, a + wa + 0.04, d - wd - 0.04, d + wd + 0.04, z1 - 0.3, z1 - 0.2, brick, back=True)
        b.fbox(F, a - wa - 0.08, a + wa + 0.08, d - wd - 0.08, d + wd + 0.08, z1 - 0.2, z1 - 0.09, brick, back=True)
        b.fbox(F, a - wa - 0.03, a + wa + 0.03, d - wd - 0.03, d + wd + 0.03, z1 - 0.09, z1, masonry, back=True)
        for side in (-0.24, 0.24):
            dress("ChimneyPot", F, a + side, z1, d=d)
        top = z1 + 0.55
    else:
        w = 0.4
        b.fbox(F, a - w, a + w, d - w, d + w, z0, z1 - 0.5, masonry, back=True)
        b.fbox(F, a - w - 0.07, a + w + 0.07, d - w - 0.07, d + w + 0.07, z1 - 0.5, z1 - 0.38, masonry, back=True)  # a drip course
        b.fbox(F, a - w + 0.03, a + w - 0.03, d - w + 0.03, d + w - 0.03, z1 - 0.38, z1 - 0.1, masonry, back=True)
        if kind == "hood":  # a slab on four stones, to keep the rain out
            b.fbox(F, a - w - 0.05, a + w + 0.05, d - w - 0.05, d + w + 0.05, z1 - 0.1, z1, masonry, back=True)
            for sa in (-1, 1):
                for sd in (-1, 1):
                    ca, cd = a + sa * (w - 0.1), d + sd * (w - 0.1)
                    b.fbox(F, ca - 0.09, ca + 0.09, cd - 0.09, cd + 0.09, z1, z1 + 0.24, masonry, back=True)
            b.fbox(F, a - w - 0.08, a + w + 0.08, d - w - 0.08, d + w + 0.08, z1 + 0.24, z1 + 0.33, masonry, back=True)
            top = z1 + 0.2
        else:
            b.fbox(F, a - w - 0.06, a + w + 0.06, d - w - 0.06, d + w + 0.06, z1 - 0.1, z1, masonry, back=True)
            dress("ChimneyPot", F, a, z1, d=d)
            top = z1 + 0.55
    x, y, _z = F.at(a, d, 0.0)
    chimney_tops.append((x, y, top))


def gabled_bay(b, F, centre, width, out, base, eaves, main, wall_mat, roof_mat, pitch, light=None, lit=False, infill="king"):
    """
    A small gabled front standing on the main wall `F`: `out` metres in front
    of it (a bay, jettied out on brackets) or behind it, up the roof (a
    dormer). Its ridge runs back into the main roof, described by `main` =
    (height of the main wall's top, tangent of the main pitch, the main slab's
    depth): the two roofs simply pass through each other, which leaves a true
    valley where they meet. Returns the height of its ridge.
    """
    main_z, main_tan, main_drop = main
    B = F.shifted(centre - width / 2, out)

    def reach(h):
        """How far behind the main wall's face the main roof is at height h."""
        return (h - main_z - main_drop) / main_tan

    openings = []
    if light is not None:
        window(B, light, width / 2, base + (0.14 if out < 0 else SILL), openings, lit)
    b.wall(B, width, base, eaves, wall_mat, openings, lining=dark_wood)
    b.fbox(B, -0.03, 0.13, 0.0, PROUD, base, eaves, dark_wood)
    b.fbox(B, width - 0.13, width + 0.03, 0.0, PROUD, base, eaves, dark_wood)
    b.fbox(B, 0.13, width - 0.13, 0.0, PROUD - 0.004, eaves - 0.14, eaves, dark_wood)
    b.fbox(B, 0.13, width - 0.13, 0.0, PROUD - 0.004, base, base + (0.2 if out > 0 else 0.1), dark_wood)
    gable_end(b, B, width, eaves, pitch, wall_mat, infill=infill)
    for a, facing in ((centre - width / 2, -F.U), (centre + width / 2, F.U)):
        if out > 0:
            b.poly([F.at(a, 0.0, base), F.at(a, out, base), F.at(a, out, eaves), F.at(a, 0.0, eaves)], wall_mat, facing)
            b.poly([F.at(a, 0.0, main_z + main_drop), F.at(a, 0.0, eaves), F.at(a, -reach(eaves), eaves)], wall_mat, facing)
            side = B.left(0.0, -out) if facing.dot(F.U) < 0 else B.right(width, 0.0)
            b.fbox(side, 0.0, out, 0.0, PROUD, base, base + 0.2, dark_wood)
            b.fbox(side, 0.0, out, 0.0, PROUD, eaves - 0.14, eaves, dark_wood)
        else:
            b.poly([F.at(a, out, base), F.at(a, out, eaves), F.at(a, -reach(eaves), eaves)], wall_mat, facing)
    if out > 0:
        b.poly([B.at(0.0, 0.0, base), B.at(width, 0.0, base), B.at(width, -out, base), B.at(0.0, -out, base)], dark_wood, -UP, grain=F.U)
        for a in (0.06, width - 0.06):  # brackets under it
            bracket = [B.at(a, -out, base - 0.6), B.at(a, -out * 0.45, base - 0.2), B.at(a, 0.0, base), B.at(a, -out, base)]
            b.prism([p - F.U * 0.06 for p in bracket], F.U * 0.12, dark_wood, back=True, grain=F.N)
    crest = eaves + width / 2 * math.tan(pitch) + 0.06 / math.cos(pitch)
    b.roof(
        B.right(width, 0.0), out + reach(crest) + 0.05, width, eaves, pitch, roof_mat,
        eave=(0.16, 0.16), verge=(0.24, 0.0), thick=0.06, under=dark_wood, barge=dark_wood, ridge=roof_mat if roof_mat is not slate else masonry,
    )
    return crest


def footing(b, x0, y0, x1, y1, low, sill):
    """The stone a building stands on, from below the lowest ground up to its sill: a plinth, its top course weathered back to the wall."""
    b.box((x0 - 0.14, y0 - 0.14, low), (x1 + 0.14, y1 + 0.14, sill - 0.12), masonry)
    for F, length in ((Frame((x0, y0), (1, 0)), x1 - x0), (Frame((x1, y0), (0, 1)), y1 - y0), (Frame((x1, y1), (-1, 0)), x1 - x0), (Frame((x0, y1), (0, -1)), y1 - y0)):
        b.poly([F.at(-0.14, 0.14, sill - 0.12), F.at(length + 0.14, 0.14, sill - 0.12), F.at(length + 0.02, 0.02, sill), F.at(-0.02, 0.02, sill)], masonry, F.N + UP)
        b.poly([F.at(-0.02, 0.02, sill), F.at(length + 0.02, 0.02, sill), F.at(length, 0.0, sill), F.at(0.0, 0.0, sill)], masonry, UP)


def house(b, lo, hi, storeys, plaster, front, chimney=False, left_open=True, right_open=True, style=None):
    """
    One house on the rectangle lo..hi (x, y). `front` is the side its door is
    on: "s", "n", "e" or "w". `left_open` and `right_open` say whether the
    sides to the left and right of someone facing the door stand free or
    against a neighbour. `style` fixes any of the choices otherwise made at
    random: roof ("gable" to the street, or "eaves"), roofing (a material),
    pitch (degrees), jetty (metres each upper storey steps out), ground
    ("timber", "stone" or "brick"), infill (the timber pattern), walls (a
    material in place of the plaster), gable (its timbers), bay, dormers, hip,
    shop, shutters, boxes, sign, lamp, pipe, lit, door, slipped (tiles),
    stack (the chimney's kind), and breast (the sides, "left" and "right",
    where a chimney breast may stand outside the wall).
    """
    style = dict(style or {})
    x0, y0, x1, y1 = lo[0], lo[1], hi[0], hi[1]
    # The floor is level, just above the highest ground it stands on; the
    # stone footing takes up the difference down the slope.
    under = [ground(x, y) for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)]
    base = max(under) + 0.1
    sill = base + 0.5  # top of the footing: where the timber starts
    top = sill + storeys * STOREY
    bare_rect((x0, y0), (x1, y1), margin=0.6)
    footing(b, x0, y0, x1, y1, min(under) - 0.6, sill)

    # The house is laid out as seen from the street: `along` from its left
    # corner to its right, `out` toward the street.
    if front == "s":
        FF, W, D = Frame((x0, y0), (1, 0)), x1 - x0, y1 - y0
    elif front == "n":
        FF, W, D = Frame((x1, y1), (-1, 0)), x1 - x0, y1 - y0
    elif front == "e":
        FF, W, D = Frame((x1, y0), (0, 1)), y1 - y0, x1 - x0
    else:
        FF, W, D = Frame((x0, y1), (0, -1)), y1 - y0, x1 - x0

    def pick(key, choose):
        value = choose()  # always drawn, so fixing one choice doesn't reshuffle the others
        return style[key] if key in style else value

    roof_kind = pick("roof", lambda: "gable" if (W < 7.4 and rng.random() < 0.5) else "eaves")
    roofing = pick("roofing", lambda: rng.choice([roof_tiles, roof_tiles, roof_mossy, slate]))
    jetty = pick("jetty", lambda: rng.choice([0.0, 0.38, 0.38, 0.46]))
    ground_kind = pick("ground", lambda: rng.choice(["timber", "timber", "timber", "stone", "brick"]))
    infill = pick("infill", lambda: rng.choice(INFILLS))
    infill_mat = style.get("walls", plaster)
    pitch = math.radians(pick("pitch", lambda: rng.choice([46, 50, 54])))
    want_bay = pick("bay", lambda: rng.random() < 0.3) and roof_kind == "eaves" and storeys >= 2 and W >= 6.4
    dormers = pick("dormers", lambda: rng.choice([0, 0, 1, 2]))
    hipped = pick("hip", lambda: rng.random() < 0.22)
    shop = pick("shop", lambda: rng.random() < 0.3)
    shutters = pick("shutters", lambda: rng.choice([None, None, "Green", "Red", "Oak"]))
    boxes = pick("boxes", lambda: rng.random() < 0.4)
    sign = pick("sign", lambda: rng.random() < 0.3)
    lamp = pick("lamp", lambda: rng.random() < 0.35)
    pipe = pick("pipe", lambda: rng.random() < 0.75)
    stack = pick("stack", lambda: rng.choice(["stone", "hood", "brick", "brick", "side"]))
    door_kind = pick("door", lambda: rng.choice(["Oak", "Oak", "Green", "Red"]))
    lit = pick("lit", lambda: rng.random() < 0.3)
    gable_infill = pick("gable", lambda: rng.choice(["king", "studs", "struts"]))
    slipped = pick("slipped", lambda: rng.random() < 0.4)
    if storeys == 1:
        jetty = 0.0
    if roof_kind != "eaves" or want_bay:
        dormers = 0

    reach = [min(jetty * k, 0.84) for k in range(storeys)]  # how far each storey's front stands out over the footing
    bays = max(2, int(W // 2.4))
    bay_width = W / bays
    door_bay = rng.randrange(bays)
    shop_bay = (door_bay + rng.choice([-1, 1])) % bays if shop else -1
    if shop_bay == door_bay:
        shop_bay = -1
    gabled = -1  # the bay of the front that carries a gabled bay under the eaves, if any
    if want_bay:
        gabled = rng.randrange(bays)
    stack_side = None
    if chimney and stack == "side" and (left_open or right_open):
        allowed = [s for s, free in (("left", left_open), ("right", right_open)) if free and s in style.get("breast", ("left", "right"))]
        stack_side = rng.choice(allowed) if allowed else None

    for level in range(storeys):
        z0, z1 = sill + level * STOREY, sill + (level + 1) * STOREY
        out = reach[level]
        timbered = level > 0 or ground_kind == "timber"
        mat = infill_mat if timbered else (masonry if ground_kind == "stone" else brick)
        jettied_above = level + 1 < storeys and reach[level + 1] > out
        plate = 0.18 if (level == storeys - 1 or jettied_above or not timbered) else 0.0
        walls = {
            "front": (FF.shifted(0.0, out), W),
            "right": (FF.right(W, out), D + out),
            "back": (FF.reverse(W, -D), W),
            "left": (FF.left(0.0, -D), D + out),
        }
        for name, (F, length) in walls.items():
            openings, posts = [], []
            free = True
            if name == "front":
                posts = [bay_width * k for k in range(1, bays)]
                for bay in range(bays):
                    centre = (bay + 0.5) * bay_width
                    if level == 0 and bay == door_bay:
                        openings.append((centre - 0.55, centre + 0.55, z0, z0 + 2.1, 0.2, dark_wood))
                        dress("Door" + door_kind, F, centre, z0)
                        # The door's frame: jambs, a heavy lintel, and brackets in its corners.
                        b.fbox(F, centre - 0.69, centre - 0.55, -0.03, 0.07, z0, z0 + 2.1, dark_wood)
                        b.fbox(F, centre + 0.55, centre + 0.69, -0.03, 0.07, z0, z0 + 2.1, dark_wood)
                        b.fbox(F, centre - 0.85, centre + 0.85, -0.03, 0.085, z0 + 2.1, z0 + 2.32, dark_wood)
                        for side in (-1, 1):
                            spandrel = [(centre + side * 0.55, z0 + 1.78), (centre + side * 0.55, z0 + 2.1), (centre + side * 0.2, z0 + 2.1)]
                            b.fprism(F, spandrel, -0.06, 0.02, dark_wood, grain=F.U)
                        # Steps down to the street, however far below it is.
                        foot = min(ground(*F.at(centre, 0.6, 0.0)[:2]), ground(*F.at(centre, 1.2, 0.0)[:2]))
                        steps = max(1, min(5, math.ceil((z0 - foot) / RISER)))  # low enough to walk up, as far as five will reach
                        for k in range(steps):
                            tread = z0 - 0.02 - (z0 - foot - 0.02) * k / steps
                            b.fbox(F, centre - 0.8 - 0.1 * k, centre + 0.8 + 0.1 * k, 0.0, 0.42 + 0.26 * k, foot - 0.7, tread, masonry)
                        if lamp:
                            dress("WallLamp", F, centre + (1.0 if bay < bays - 1 else -1.0), z0 + 2.05)
                    elif level == 0 and bay == shop_bay:
                        window(F, "S", centre, z0 + SILL - 0.1, openings)
                        dress("Awning", F, centre, z0 + 2.62, colour=rng.randrange(3))
                    elif level == storeys - 1 and bay == gabled:
                        pass  # the gabled bay stands in front of this stretch of wall
                    else:
                        kind = "B" if (level > 0 and bay_width > 2.9 and infill in ("square", "close")) else "A"
                        window(F, kind, centre, z0 + SILL, openings, lit=lit and level == storeys - 1 and bay == bays - 1 - door_bay)
                        if kind == "A" and shutters and (level > 0 or shop_bay < 0):
                            dress("Shutter" + shutters, F, centre - 0.78, z0 + SILL)
                            dress("Shutter" + shutters, F, centre + 0.78, z0 + SILL)
                        if kind == "A" and boxes and level == 1:
                            dress("FlowerBox", F, centre, z0 + SILL, variant=rng.randrange(2))
                if sign and level == min(1, storeys - 1):
                    dress("Sign", F, bay_width * (door_bay + (1 if door_bay < bays - 1 else 0)), z0 + (2.75 if storeys == 1 else 0.75), variant=rng.randrange(2))
            elif name == "back":
                posts = [bay_width * k for k in range(1, bays)]
                for bay in range(bays):
                    if rng.random() < 0.72:
                        window(F, "A" if rng.random() < 0.7 else "C", (bay + 0.5) * bay_width, z0 + SILL, openings)
            else:
                free = left_open if name == "left" else right_open
                posts = [length * k / 3 for k in (1, 2)]
                if free:
                    for k in (0, 1, 2):
                        if k == 1 and stack_side == name:
                            continue  # the chimney breast is here
                        if rng.random() < 0.5:
                            window(F, "A" if rng.random() < 0.6 else "C", length * (2 * k + 1) / 6, z0 + SILL, openings)
            b.wall(F, length, z0, z1, mat, openings, lining=dark_wood)
            if timbered:
                timber_wall(b, F, length, z0, z1, openings, posts, infill if free else "posts", plate=plate)
            else:
                masonry_wall(b, F, length, openings, mat)
                b.fbox(F, 0.0, length, 0.0, PROUD, z1 - plate, z1, dark_wood)
        # Corner posts, standing a little proud of both walls they join; quoins on a stone or brick ground floor.
        for a0, a1 in ((-0.06, 0.2), (W - 0.2, W + 0.06)):
            for d0, d1 in ((out - 0.2, out + 0.06), (-D - 0.06, -D + 0.2)):
                if timbered:
                    b.box(*sorted_box(FF.at(a0, d0, z0), FF.at(a1, d1, z1)), dark_wood, grain=(0, 0, 1))
                elif d1 > 0:
                    course, long = z0, a0 < 0
                    while course + 0.3 <= z1 - plate + 0.01:
                        la, ld = (0.52, 0.3) if long else (0.3, 0.52)
                        qa0, qa1 = (-0.035, la) if a0 < 0 else (W - la, W + 0.035)
                        b.box(*sorted_box(FF.at(qa0, out - ld, course + 0.012), FF.at(qa1, out + 0.035, course + 0.288)), masonry)
                        course, long = course + 0.3, not long
        # A jetty: the floor above carried out over the street on the ends of its joists.
        if level > 0 and out > reach[level - 1]:
            inner = reach[level - 1]
            b.poly([FF.at(0.0, inner, z0), FF.at(W, inner, z0), FF.at(W, out, z0), FF.at(0.0, out, z0)], dark_wood, -UP, grain=FF.U)
            b.fbox(FF, 0.0, W, out, out + 0.07, z0 - 0.02, z0 + 0.26, dark_wood)  # the bressummer
            joists = max(3, round(W / 0.55))
            for k in range(joists):
                a = 0.14 + (W - 0.28) * k / (joists - 1)
                end = [FF.at(a - 0.07, inner, z0 - 0.17), FF.at(a + 0.07, inner, z0 - 0.17), FF.at(a + 0.07, inner, z0), FF.at(a - 0.07, inner, z0)]
                b.prism(end, FF.N * (out - inner + 0.05), dark_wood, sides=[True, True, False, True], grain=FF.N)
            for a in [0.07] + [bay_width * k for k in range(1, bays)] + [W - 0.07]:
                bracket = [FF.at(a, inner + PROUD, z0 - 0.75), FF.at(a, inner + (out - inner) * 0.5, z0 - 0.3), FF.at(a, out + 0.04, z0 - 0.17), FF.at(a, inner + PROUD, z0 - 0.17)]
                b.prism([p - FF.U * 0.06 for p in bracket], FF.U * 0.12, dark_wood, back=True, grain=FF.N + UP)

    # The roof.
    out = reach[-1]
    tan = math.tan(pitch)
    thick = 0.3 if roofing is thatch else 0.07  # thatch is laid thick, in courses, under a rolled ridge
    drop = thick / math.cos(pitch)
    if roof_kind == "eaves":
        RF, ridge_length, span = FF.shifted(0.0, out), W, D + out
        verge = (0.36 if left_open else 0.1, 0.36 if right_open else 0.1)
        eave = (0.5, 0.42)
        hip = (0.42 if hipped and left_open else 0.0, 0.42 if hipped and right_open else 0.0)
        shown = (left_open, right_open)
    else:
        RF, ridge_length, span = FF.right(W, out), D + out, W  # runs back from the street: its left end is the street gable
        verge = (0.42, 0.3)
        eave = (0.46 if right_open else 0.12, 0.46 if left_open else 0.12)
        hip = (0.4 if hipped else 0.0, 0.0)
        shown = (True, True)
    if hip[0] or hip[1]:
        eave = (eave[0], eave[0])
    ridge_mat = roofing if roofing is not slate else masonry
    crest, surface = b.roof(
        RF, ridge_length, span, top, pitch, roofing, eave=eave, verge=verge, hip=hip, thick=thick, under=dark_wood, barge=dark_wood, tails=dark_wood,
        ridge=ridge_mat, purlins=dark_wood, course=2.1 if roofing is thatch else None, ridge_size=(0.5, 0.15, 1.2) if roofing is thatch else (0.17, 0.07, 0.42),
        tile_ends=CLAY_COLUMNS if roofing is roof_tiles else None, verge_tiles=roofing if roofing in (roof_tiles, roof_mossy) else None,
    )
    if slipped and roofing is not thatch:
        # A few tiles that have slipped: lifted, skewed, and slid a little down the slope.
        cos = math.cos(pitch)
        for _ in range(rng.randint(2, 5)):
            s, c, skew = rng.uniform(0.7, ridge_length - 0.7), rng.uniform(0.3, span / 2 - 0.7), rng.uniform(-0.35, 0.35)
            corners = []
            for ds, dl in ((-0.15, -0.2), (0.15, -0.2), (0.15, 0.2), (-0.15, 0.2)):
                rs, rl = ds * math.cos(skew) - dl * math.sin(skew), ds * math.sin(skew) + dl * math.cos(skew)
                corners.append(RF.at(s + rs, -(c + rl * cos), surface(c + rl * cos) + 0.05 - 0.07 * (dl + 0.2)))
            b.prism(corners, UP * -0.03, roofing, front=False, back=True, grain=corners[1] - corners[0])
    for e, G in enumerate((RF.left(0.0, -span), RF.right(ridge_length, 0.0))):
        cap = None
        if hip[e] > 0:  # the wall stops where the half hip's underside comes down to it
            cap = top + (span / 2 - hip[e] * (span / 2 + eave[0]) + verge[e]) * tan
        gable_end(b, G, span, top, pitch, infill_mat, infill=gable_infill, cap=cap, light="C" if shown[e] else None)

    if roof_kind == "eaves":
        main = (top, tan, drop)
        if gabled >= 0:
            gabled_bay(b, RF, (gabled + 0.5) * bay_width, min(2.7, bay_width + 0.3), 0.55, top - STOREY, top, main, infill_mat, roofing, math.radians(52), light="B", infill=gable_infill)
        for k in range(dormers):
            centre = W * (k + 1) / (dormers + 1) + (rng.uniform(-0.4, 0.4) if dormers == 1 else 0.0)
            back = 0.75
            foot = top + back * tan + drop
            if foot + 1.2 + 0.65 * math.tan(math.radians(50)) + 0.4 < crest:
                gabled_bay(b, RF, centre, 1.3, -back, foot, foot + 1.2, main, infill_mat, roofing, math.radians(50), light="C", infill="plain")
        if pipe and jetty == 0.0:
            # A wooden gutter under the eaves, and a pipe down one corner.
            edge = eave[0] + 0.02
            low = top - eave[0] * tan - 0.03
            b.fbox(RF, -0.1, W + 0.1, edge - 0.02, edge + 0.12, low - 0.11, low, dark_wood, back=True)
            corner = 0.22 if rng.random() < 0.5 else W - 0.22
            foot = ground(*RF.at(corner, 0.2, 0.0)[:2])
            dress("Drainpipe", RF, corner, foot, tall=low - 0.1 - foot)

    if chimney:
        if stack_side:
            # A chimney breast up the outside of a free side wall, stepping in to its stack.
            S = FF.left(0.0, -D) if stack_side == "left" else FF.right(W, 0.0)
            a = D / 2
            shoulder = sill + (STOREY * 0.75 if storeys == 1 else STOREY * (storeys - 1) + 1.0)
            head = crest + 0.9 if roof_kind == "eaves" else min(crest + 0.4, top + 3.4)
            foot = ground(*S.at(a, 0.3, 0.0)[:2]) - 0.6
            b.fbox(S, a - 0.8, a + 0.8, 0.0, 0.46, foot, shoulder, masonry)
            b.fprism(S, [(a - 0.8, shoulder), (a + 0.8, shoulder), (a + 0.44, shoulder + 0.55), (a - 0.44, shoulder + 0.55)], 0.0, 0.46, masonry)
            b.fbox(S, a - 0.86, a + 0.86, 0.0, 0.52, foot, base + 0.38, masonry)
            b.fbox(S, a - 0.44, a + 0.44, -0.3, 0.46, shoulder + 0.55, head - 0.34, masonry, back=True)
            b.fbox(S, a - 0.5, a + 0.5, -0.36, 0.52, head - 0.34, head - 0.22, masonry, back=True)
            b.fbox(S, a - 0.4, a + 0.4, -0.26, 0.42, head - 0.22, head - 0.1, masonry, back=True)
            b.fbox(S, a - 0.5, a + 0.5, -0.36, 0.52, head - 0.1, head, masonry, back=True)
            dress("ChimneyPot", S, a - 0.2, head, d=0.08)
            dress("ChimneyPot", S, a + 0.2, head, d=0.08)
            cx, cy, _z = S.at(a, 0.08, 0.0)
            chimney_tops.append((cx, cy, head + 0.55))
        else:
            kind = stack if stack != "side" else "stone"
            s = ridge_length * rng.uniform(0.3, 0.7)
            c = span / 2 + (rng.choice([-1, 1]) * rng.uniform(0.5, 1.1) if rng.random() < 0.5 else 0.0)
            chimney_stack(b, RF, s, -c, top, crest + rng.uniform(0.75, 1.1), kind)


def row_of_houses(name, start, end, depth, front):
    """
    Houses side by side filling the strip from `start` to `end` (along X for
    fronts facing north or south, along Y otherwise), `depth` metres deep,
    with the odd narrow alley between them. One object.

    The row is planned before it is built, so that each house knows whether
    it has neighbours and so that neighbours differ: in colour, and in height
    or in which way the roof runs or what it is covered with. A row keeps to
    three plasters and two kinds of roofing, since every material in it is a
    draw call.
    """
    b = Builder([])
    along_x = front in ("s", "n")
    position = start[0] if along_x else start[1]
    limit = end[0] if along_x else end[1]
    fixed = start[1] if along_x else start[0]
    plots = []
    while limit - position > 5.0:
        width = min(rng.uniform(5.5, 8.5), limit - position)
        if limit - position - width < 5.0:
            width = limit - position  # no sliver left at the end
        inset = rng.uniform(0.0, 1.2)  # how far this one stands back from the street
        storeys = rng.choice([1, 2, 2, 2, 3])
        alley = rng.choice([0.0, 0.0, 1.6, 2.2])  # sometimes an alley after it
        plots.append((position, width, inset, storeys, alley))
        position += width + alley
    plasters = rng.sample(PLASTERS, 3)
    roofings = [roof_tiles, rng.choice([roof_mossy, slate])]
    previous = None
    for i, (position, width, inset, storeys, alley) in enumerate(plots):
        if along_x:
            y_front = fixed + (inset if front == "s" else -inset)
            lo = (position, min(y_front, y_front + (depth if front == "s" else -depth)))
            hi = (position + width, max(y_front, y_front + (depth if front == "s" else -depth)))
        else:
            x_front = fixed + (inset if front == "w" else -inset)
            lo = (min(x_front, x_front + (depth if front == "w" else -depth)), position)
            hi = (max(x_front, x_front + (depth if front == "w" else -depth)), position + width)
        before = i == 0 or plots[i - 1][4] > 0
        after = i == len(plots) - 1 or alley > 0
        left_open, right_open = (before, after) if front in ("s", "e") else (after, before)
        plaster = rng.choice([p for p in plasters if previous is None or p is not previous["plaster"]])
        roof_kind = "gable" if (width < 7.4 and rng.random() < 0.5) else "eaves"
        if previous and previous["roof"] == roof_kind and previous["storeys"] == storeys and width < 7.4:
            roof_kind = "gable" if roof_kind == "eaves" else "eaves"  # the same height as next door: turn the roof
        roofing = rng.choice([roofings[0], roofings[0], roofings[1]])
        if previous and previous["storeys"] == storeys and previous["roof"] == roof_kind:
            roofing = roofings[1] if previous["roofing"] is roofings[0] else roofings[0]
        first, last = (i == 0, i == len(plots) - 1) if front in ("s", "e") else (i == len(plots) - 1, i == 0)
        breast = [side for side, at_end in (("left", first), ("right", last)) if at_end]  # not out into an alley: it would block it
        style = {"roof": roof_kind, "roofing": roofing, "breast": breast}
        house(b, lo, hi, storeys, plaster, front, chimney=rng.random() < 0.6, left_open=left_open, right_open=right_open, style=style)
        previous = {"plaster": plaster, "roof": roof_kind, "storeys": storeys, "roofing": roofing}
    add(name, b.mesh(name), town, lod_ratio=1.0)


# Fronts face the street they stand on. The strips leave a yard behind each
# row, between it and the town wall.
S, L, Q = STREET_HALF + 0.6, LANE_HALF + 0.6, SQUARE_HALF + 0.6
row_of_houses("HousesMainWest", (-S, -WALL + 12), (-S, -Q - 1), 8.0, "e")  # west side of the main street
row_of_houses("HousesMainEast", (S, -WALL + 12), (S, -Q - 1), 8.0, "w")
row_of_houses("HousesLaneWestS", (-WALL + 8, -L), (-Q - 1, -L), 7.0, "n")  # south side of the west lane
row_of_houses("HousesLaneWestN", (-WALL + 8, L), (-Q - 1, L), 7.0, "s")
row_of_houses("HousesLaneEastS", (Q + 1, -L), (WALL - 8, -L), 7.0, "n")
row_of_houses("HousesLaneEastN", (Q + 1, L), (WALL - 8, L), 7.0, "s")
# Facing the square from the north, leaving room for the braziers either side of the hall steps.
row_of_houses("HousesSquareNW", (-Q - 11, Q + 3), (-S - 3.5, Q + 3), 7.5, "s")
row_of_houses("HousesSquareNE", (S + 3.5, Q + 3), (Q + 11, Q + 3), 7.5, "s")
row_of_houses("HousesBackWest", (-WALL + 11, -WALL + 14), (-WALL + 11, -L - 12), 6.0, "e")  # a back row by the west wall
row_of_houses("HousesBackEast", (WALL - 11, -WALL + 14), (WALL - 11, -L - 12), 6.0, "w")


# --- The hall ----------------------------------------------------------------
# Stone, on a raised terrace at the north end, up a broad flight of steps:
# buttressed walls with pointed windows, their tracery and surrounds in a paler
# dressed stone, a corbelled eaves course, gables that rise above the roof
# under stone copings, dormers and a little spire on the ridge, and a bell
# tower at its east end.

dressed = PLASTERS[3]  # pale dressed stone, for what a mason would have cut: surrounds, quoins, copings, string courses


def pointed_arch(left, right, spring, steps=4):
    """A pointed arch's outline: [(along, height), ...] from its left foot over the apex to its right foot."""
    wide = right - left
    points = []
    for k in range(steps + 1):  # the left side is struck from the right foot, and the right side from the left
        t = math.radians(180 - 60 * k / steps)
        points.append((right + wide * math.cos(t), spring + wide * math.sin(t)))
    for k in range(1, steps + 1):
        t = math.radians(60 - 60 * k / steps)
        points.append((left + wide * math.cos(t), spring + wide * math.sin(t)))
    return points


def lancet(b, F, centre, wide, foot, spring, depth, closing, wall_mat, lining=None, trim=0.16, tracery=True, d=0.0):
    """
    A pointed opening in a wall, in the rectangular hole left for it by
    `Builder.wall` (from `foot` to `lancet_top(wide, spring)`): the wall's
    corners above the arch, the reveals, what closes the back, a dressed
    surround standing proud of the wall, and stone tracery in front of the glass.
    """
    left, right = centre - wide / 2, centre + wide / 2
    arch = pointed_arch(left, right, spring)
    half = len(arch) // 2
    apex = arch[half][1]
    for corner, side in (((left, apex), arch[: half + 1]), ((right, apex), arch[half:])):
        for p, q in zip(side[:-1], side[1:]):
            b.poly([F.at(corner[0], d, corner[1]), F.at(p[0], d, p[1]), F.at(q[0], d, q[1])], wall_mat, F.N)
    outline = [(left, foot)] + arch + [(right, foot)]
    middle = Vector(F.at(centre, d, (foot + apex) / 2))
    for p, q in zip(outline, outline[1:] + outline[:1]):
        edge = [F.at(p[0], d, p[1]), F.at(q[0], d, q[1]), F.at(q[0], d - depth, q[1]), F.at(p[0], d - depth, p[1])]
        b.poly(edge, lining or wall_mat, middle - (Vector(edge[0]) + Vector(edge[1])) / 2)
    b.poly([F.at(a, d - depth, z) for a, z in outline], closing, F.N, grain=UP)
    if trim:
        # The surround: the same arch struck a little wider, and jambs down to the sill.
        outer = []
        for k, (a, z) in enumerate(arch):
            if k == half:
                outer.append((centre, spring + math.sqrt((wide + trim) ** 2 - (wide / 2) ** 2)))
            else:
                cx = right if k < half else left
                scale = (wide + trim) / wide
                outer.append((cx + (a - cx) * scale, spring + (z - spring) * scale))
        for k in range(len(arch) - 1):
            b.fprism(F, [arch[k], arch[k + 1], outer[k + 1], outer[k]], d, d + 0.06, dressed)
        b.fbox(F, left - trim, left, d, d + 0.06, foot, spring, dressed)
        b.fbox(F, right, right + trim, d, d + 0.06, foot, spring, dressed)
    if tracery:
        back = d - depth
        b.fbox(F, centre - 0.06, centre + 0.06, back, back + 0.13, foot, spring + 0.05, dressed)
        for side in (-1, 1):  # the mullion parts in a Y to meet the arch
            a = centre + side * wide * 0.27
            cx = right if side < 0 else left
            z = spring + math.sqrt(wide**2 - (a - cx) ** 2)
            b.fprism(F, [(centre - 0.055, spring), (centre + 0.055, spring), (a + 0.055, z), (a - 0.055, z)], back, back + 0.12, dressed)
        b.fbox(F, left, right, back, back + 0.11, (foot + spring) / 2 - 0.05, (foot + spring) / 2 + 0.05, dressed)
        b.fbox(F, left - 0.18, right + 0.18, d, d + 0.1, foot - 0.14, foot, dressed)  # the sill


def lancet_top(wide, spring):
    return spring + wide * math.sin(math.radians(60))


def buttress(b, F, a, wide, foot, top, deep=0.7):
    """A stepped buttress against a wall: two stages, each weathered back at its head."""
    step = foot + (top - foot) * 0.52
    profile = [(0.0, foot), (deep, foot), (deep, step), (deep * 0.55, step + 0.45), (deep * 0.55, top - 0.55), (0.0, top)]
    b.prism([F.at(a - wide / 2, d, z) for d, z in profile], F.U * wide, masonry, back=True)
    b.fbox(F, a - wide / 2 - 0.08, a + wide / 2 + 0.08, 0.0, deep + 0.08, foot, foot + 0.5, masonry)


def stone_wall(b, F, length, z0, z1, lancets=(), slits=(), door=None, plinth=True, corbels=True, string=None):
    """
    One wall of a stone building: `lancets` are (centre, width, sill, spring)
    windows, `slits` (centre, bottom, top) narrow lights, `door` a (centre,
    width, spring) portal. With a plinth at its foot, a string course, and a
    corbel table under the eaves.
    """
    openings, cuts_a, cuts_z = [], [], []
    for centre, wide, foot, spring in lancets:
        openings.append((centre - wide / 2, centre + wide / 2, foot, lancet_top(wide, spring), None, None))
        cuts_a.append(centre)
        cuts_z.append(spring)
    if door:
        centre, wide, spring = door
        openings.append((centre - wide / 2, centre + wide / 2, z0, lancet_top(wide, spring), None, None))
        cuts_a.append(centre)
        cuts_z.append(spring)
    for centre, bottom, top in slits:
        openings.append((centre - 0.13, centre + 0.13, bottom, top, 0.4, dark_wood))
    b.wall(F, length, z0, z1, masonry, openings, cuts=(cuts_a, cuts_z))
    for centre, wide, foot, spring in lancets:
        lancet(b, F, centre, wide, foot, spring, 0.38, glass, masonry)
    for centre, bottom, top in slits:
        b.fbox(F, centre - 0.25, centre + 0.25, 0.0, 0.05, top, top + 0.2, dressed)
        b.fbox(F, centre - 0.25, centre + 0.25, 0.0, 0.07, bottom - 0.12, bottom, dressed)
    gaps = sorted((o[0] - 0.4, o[1] + 0.4) for o in openings if o[2] <= z0 + 0.6)  # where a door interrupts the courses

    def course(lo, hi, deep, mat):
        at = 0.0
        for g0, g1 in gaps + [(length, length)]:
            if g0 > at:
                b.fbox(F, at, g0, 0.0, deep, lo, hi, mat)
            at = max(at, g1)

    if plinth:
        course(z0, z0 + 0.55, 0.12, masonry)
        course(z0 + 0.55, z0 + 0.67, 0.07, dressed)
    if string is not None:
        course(string - 0.12, string, 0.07, dressed)
    if corbels:
        b.fbox(F, 0.0, length, 0.0, 0.16, z1 - 0.22, z1, dressed)
        count_ = round(length / 0.62)
        for k in range(count_):
            a = (k + 0.5) * length / count_
            b.fprism(F, [(a - 0.08, z1 - 0.48), (a + 0.08, z1 - 0.48), (a + 0.08, z1 - 0.22), (a - 0.08, z1 - 0.22)], 0.0, 0.13, masonry, sides=[True, True, False, True])


def quoins(b, F, length, z0, z1, ends=(True, True), tall=0.42):
    """Dressed stones up the corners at either end of a wall, long and short by turns."""
    course, long = z0, True
    while course + tall <= z1 + 0.01:
        wide = 0.62 if long else 0.36
        if ends[0]:
            b.fbox(F, -0.04, wide, 0.0, 0.04, course + 0.015, course + tall - 0.015, dressed, sides=True)
        if ends[1]:
            b.fbox(F, length - (0.98 - wide), length + 0.04, 0.0, 0.04, course + 0.015, course + tall - 0.015, dressed)
        course, long = course + tall, not long


hall = Builder([masonry])
hall.box((-TERRACE_HALF, TERRACE_FRONT, -0.6), (TERRACE_HALF, TERRACE_BACK, TERRACE), masonry)
hall.stairs((0.0, STAIR_FOOT_Y, HALL_GROUND), (0, 1), TERRACE_STEPS, 9.0, masonry)
bare_rect((-TERRACE_HALF, STAIR_FOOT_Y), (TERRACE_HALF, TERRACE_BACK))
# A parapet round the terrace, open at the head of the steps, and a wall down either side of the flight.
STAIR_HALF = 4.5
terrace_front = Frame((-TERRACE_HALF, TERRACE_FRONT), (1, 0))
for a0, a1 in ((0.0, TERRACE_HALF - STAIR_HALF - 0.5), (TERRACE_HALF + STAIR_HALF + 0.5, 2 * TERRACE_HALF)):
    hall.fbox(terrace_front, a0, a1, -0.38, 0.0, TERRACE, TERRACE + 0.85, masonry, back=True)
    hall.fbox(terrace_front, a0 - 0.05, a1 + 0.05, -0.44, 0.06, TERRACE + 0.85, TERRACE + 0.97, dressed, back=True)
for F, length in ((Frame((TERRACE_HALF, TERRACE_FRONT), (0, 1)), TERRACE_BACK - TERRACE_FRONT), (Frame((-TERRACE_HALF, TERRACE_BACK), (0, -1)), TERRACE_BACK - TERRACE_FRONT)):
    hall.fbox(F, 0.0, length, -0.38, 0.0, TERRACE, TERRACE + 0.85, masonry, back=True)
    hall.fbox(F, -0.05, length + 0.05, -0.44, 0.06, TERRACE + 0.85, TERRACE + 0.97, dressed, back=True)
for a0, a1 in ((-0.06, TERRACE_HALF - STAIR_HALF - 0.5), (TERRACE_HALF + STAIR_HALF + 0.5, 2 * TERRACE_HALF + 0.06)):
    hall.fbox(terrace_front, a0, a1, 0.0, 0.07, TERRACE - 0.2, TERRACE, dressed)
for a in (0.0, TERRACE_HALF - STAIR_HALF - 0.25, TERRACE_HALF + STAIR_HALF + 0.25, 2 * TERRACE_HALF):  # piers
    lo, hi = max(a - 0.3, -0.1), min(a + 0.3, 2 * TERRACE_HALF + 0.1)
    hall.fbox(terrace_front, lo, hi, -0.5, 0.1, TERRACE - 0.26, TERRACE + 1.15, masonry, back=True)
    hall.fbox(terrace_front, lo - 0.05, hi + 0.05, -0.55, 0.15, TERRACE + 1.15, TERRACE + 1.27, dressed, back=True)
for side in (-1, 1):
    x = side * (STAIR_HALF + 0.25)
    flank = [(STAIR_FOOT_Y - 0.25, HALL_GROUND - 0.8), (TERRACE_FRONT, HALL_GROUND - 0.8), (TERRACE_FRONT, TERRACE + 0.85), (STAIR_FOOT_Y - 0.25, HALL_GROUND + 0.75)]
    hall.prism([(x - 0.25, y, z) for y, z in flank], (0.5, 0, 0), masonry, back=True)
    cope = [(STAIR_FOOT_Y - 0.31, HALL_GROUND + 0.74), (TERRACE_FRONT, TERRACE + 0.85), (TERRACE_FRONT, TERRACE + 0.97), (STAIR_FOOT_Y - 0.31, HALL_GROUND + 0.86)]
    hall.prism([(x - 0.31, y, z) for y, z in cope], (0.62, 0, 0), dressed, back=True)

HALL_FRONT, HALL_BACK, HALL_HALF, HALL_WALLS = 33.0, 44.0, 9.0, 7.0
HALL_TOP = TERRACE + HALL_WALLS
HALL_PITCH = math.radians(50)
hall_south = Frame((-HALL_HALF, HALL_FRONT), (1, 0))
hall_east = Frame((HALL_HALF, HALL_FRONT), (0, 1))
hall_north = Frame((HALL_HALF, HALL_BACK), (-1, 0))
hall_west = Frame((-HALL_HALF, HALL_BACK), (0, -1))
HALL_WIDE, HALL_DEEP = 2 * HALL_HALF, HALL_BACK - HALL_FRONT
SILL_Z, SPRING_Z = TERRACE + 2.1, TERRACE + 4.5
# South: the great door between buttresses, two windows either side.
stone_wall(
    hall, hall_south, HALL_WIDE, TERRACE, HALL_TOP,
    lancets=[(HALL_HALF + x, 1.3, SILL_Z, SPRING_Z) for x in (-6.9, -3.9, 3.9, 6.9)], door=(HALL_HALF, 2.6, TERRACE + 2.4), string=SILL_Z - 0.2,
)
lancet(hall, hall_south, HALL_HALF, 2.6, TERRACE, TERRACE + 2.4, 0.6, wood, masonry, trim=0.3, tracery=False)
door_back = hall_south.shifted(0.0, -0.6)
hall.fbox(door_back, HALL_HALF - 0.07, HALL_HALF + 0.07, 0.0, 0.07, TERRACE, TERRACE + 2.4 + 2.2, dark_wood)  # where the two leaves meet
for z in (0.45, 1.4, 2.35):
    for side in (-1, 1):
        hall.fbox(door_back, *sorted((HALL_HALF + side * 1.28, HALL_HALF + side * 0.3)), 0.0, 0.03, TERRACE + z, TERRACE + z + 0.09, iron)
for side in (-1, 1):  # a pull on each leaf
    hall.fbox(door_back, *sorted((HALL_HALF + side * 0.2, HALL_HALF + side * 0.32)), 0.0, 0.05, TERRACE + 1.05, TERRACE + 1.3, iron)
hall.fbox(hall_south, HALL_HALF - 2.1, HALL_HALF + 2.1, 0.0, 0.5, TERRACE - 0.3, TERRACE + 0.02, masonry)  # the threshold
for x in (-8.65, -5.4, -2.3, 2.3, 5.4, 8.65):
    buttress(hall, hall_south, HALL_HALF + x, 0.62, TERRACE, HALL_TOP - 0.5)
# North: five windows, six buttresses.
stone_wall(hall, hall_north, HALL_WIDE, TERRACE, HALL_TOP, lancets=[(HALL_HALF + x, 1.3, SILL_Z, SPRING_Z) for x in (-6.6, -3.3, 0.0, 3.3, 6.6)], string=SILL_Z - 0.2)
for x in (-8.65, -4.95, -1.65, 1.65, 4.95, 8.65):
    buttress(hall, hall_north, HALL_HALF + x, 0.62, TERRACE, HALL_TOP - 0.5)
# West: the great window in the gable end.
stone_wall(hall, hall_west, HALL_DEEP, TERRACE, HALL_TOP, lancets=[(HALL_DEEP / 2, 2.4, SILL_Z, TERRACE + 4.3)], string=SILL_Z - 0.2, corbels=False)
for a in (0.35, 3.3, HALL_DEEP - 3.3, HALL_DEEP - 0.35):
    buttress(hall, hall_west, a, 0.62, TERRACE, HALL_TOP - 0.5)
# East: the tower stands against the southern half of it.
stone_wall(hall, hall_east, HALL_DEEP, TERRACE, HALL_TOP, lancets=[(8.4, 1.3, SILL_Z, SPRING_Z)], string=SILL_Z - 0.2, corbels=False)
buttress(hall, hall_east, HALL_DEEP - 0.35, 0.62, TERRACE, HALL_TOP - 0.5)
buttress(hall, hall_east, 6.6, 0.62, TERRACE, HALL_TOP - 0.5)

hall_tan = math.tan(HALL_PITCH)
hall_drop = 0.09 / math.cos(HALL_PITCH)
hall_crest, hall_surface = hall.roof(
    hall_south, HALL_WIDE, HALL_DEEP, HALL_TOP, HALL_PITCH, slate, eave=(0.5, 0.5), verge=(-0.02, -0.02), thick=0.09, under=dark_wood, ridge=masonry
)
# The gables stand up above the roof under dressed copings, with a kneeler at each foot and a finial at the peak.
LIFT = hall_drop + 0.45
for G in (hall_west, hall_east):
    rise = HALL_DEEP / 2 * hall_tan
    outline = [(0.0, HALL_TOP), (HALL_DEEP, HALL_TOP), (HALL_DEEP, HALL_TOP + LIFT), (HALL_DEEP / 2, HALL_TOP + rise + LIFT), (0.0, HALL_TOP + LIFT)]
    hall.fprism(G, outline, -0.5, 0.0, masonry, back=True)
    thick = 0.13 / math.cos(HALL_PITCH)
    for side in (0, 1):
        foot = -0.1 if side == 0 else HALL_DEEP + 0.1
        low = HALL_TOP + LIFT - 0.1 * hall_tan
        cope = [(foot, low), (HALL_DEEP / 2, HALL_TOP + rise + LIFT), (HALL_DEEP / 2, HALL_TOP + rise + LIFT + thick), (foot, low + thick)]
        hall.fprism(G, cope, -0.57, 0.07, dressed, back=True)
        a0, a1 = (-0.62, 0.3) if side == 0 else (HALL_DEEP - 0.3, HALL_DEEP + 0.62)
        hall.fbox(G, a0, a1, -0.57, 0.07, HALL_TOP - 0.62, HALL_TOP + LIFT + 0.12, dressed, back=True)
    peak = HALL_TOP + rise + LIFT + thick
    hall.fbox(G, HALL_DEEP / 2 - 0.16, HALL_DEEP / 2 + 0.16, -0.41, -0.09, peak - 0.1, peak + 0.22, dressed, back=True)
    hall.fbox(G, HALL_DEEP / 2 - 0.07, HALL_DEEP / 2 + 0.07, -0.32, -0.18, peak + 0.22, peak + 0.95, dressed, back=True)
    hall.fbox(G, HALL_DEEP / 2 - 0.26, HALL_DEEP / 2 + 0.26, -0.32, -0.18, peak + 0.55, peak + 0.69, dressed, back=True)
# Dormers on both slopes, and a small spire astride the ridge.
hall_main = (HALL_TOP, hall_tan, hall_drop)
for F, places in ((hall_south, (-5.4, 0.0, 5.4)), (hall_north, (-3.6, 3.6))):
    for x in places:
        foot = HALL_TOP + 1.6 * hall_tan + hall_drop
        gabled_bay(hall, F, HALL_HALF + x, 1.4, -1.6, foot, foot + 1.25, hall_main, dressed, slate, math.radians(55), light="C", infill="plain")
spire = (0.0, (HALL_FRONT + HALL_BACK) / 2)
hall.cylinder(spire, 0.62, hall_crest - 0.5, hall_crest + 0.35, masonry, segments=8)
hall.cylinder(spire, 0.5, hall_crest + 0.35, hall_crest + 1.5, dark_wood, segments=8)
hall.cylinder(spire, 0.72, hall_crest + 1.5, hall_crest + 1.62, dark_wood, segments=8)
hall.cylinder(spire, 0.68, hall_crest + 1.62, hall_crest + 4.6, slate, segments=8, top_radius=0.03)
hall.cylinder(spire, 0.025, hall_crest + 4.5, hall_crest + 5.5, iron, segments=5)
hall.box((spire[0] - 0.3, spire[1] - 0.012, hall_crest + 5.1), (spire[0] + 0.3, spire[1] + 0.012, hall_crest + 5.18), iron)
chimney_stack(hall, hall_north, HALL_HALF + 6.0, -1.5, HALL_TOP, hall_surface(1.5) + 2.6, "hood")

# The bell tower: three stages marked by string courses, louvred openings to the bells, a corbelled head and a steep slate cap.
TOWER_X, BELL_HALF, BELL_TOP = 11.5, 2.4, TERRACE + 18.0
BELL_SIDE = 2 * BELL_HALF
tower_y = HALL_FRONT + 1.0
belfry = BELL_TOP - 4.3
tower_faces = [
    Frame((TOWER_X - BELL_HALF, tower_y), (1, 0)),
    Frame((TOWER_X + BELL_HALF, tower_y), (0, 1)),
    Frame((TOWER_X + BELL_HALF, tower_y + BELL_SIDE), (-1, 0)),
    Frame((TOWER_X - BELL_HALF, tower_y + BELL_SIDE), (0, -1)),
]
for i, F in enumerate(tower_faces):
    slits = [(BELL_HALF, TERRACE + 2.6, TERRACE + 4.0), (BELL_HALF, TERRACE + 8.2, TERRACE + 9.6)] if i != 3 else []
    louvres = [(BELL_HALF + x, 0.8, belfry + 0.7, belfry + 2.5) for x in (-0.85, 0.85)]
    openings = [(c - w / 2, c + w / 2, foot, lancet_top(w, spring), None, None) for c, w, foot, spring in louvres]
    openings += [(c - 0.13, c + 0.13, bottom, top, 0.4, dark_wood) for c, bottom, top in slits]
    hall.wall(F, BELL_SIDE, TERRACE, BELL_TOP, masonry, openings, cuts=([c for c, _w, _f, _s in louvres], [s for _c, _w, _f, s in louvres]))
    for centre, bottom, top in slits:
        hall.fbox(F, centre - 0.25, centre + 0.25, 0.0, 0.05, top, top + 0.2, dressed)
        hall.fbox(F, centre - 0.25, centre + 0.25, 0.0, 0.07, bottom - 0.12, bottom, dressed)
    for centre, wide, foot, spring in louvres:
        lancet(hall, F, centre, wide, foot, spring, 0.45, dark_wood, masonry, trim=0.13, tracery=False)
        slats = 7
        for k in range(slats):  # louvre boards, tilted to shed the rain
            z = foot + 0.1 + (spring + 0.25 - foot) * k / slats
            board = [F.at(centre - wide / 2, -0.3, z + 0.2), F.at(centre + wide / 2, -0.3, z + 0.2), F.at(centre + wide / 2, -0.06, z), F.at(centre - wide / 2, -0.06, z)]
            hall.prism(board, (board[1] - board[0]).cross(board[3] - board[0]).normalized() * 0.03, wood, back=True, sides=[False, False, True, False], grain=F.U)
        hall.fbox(F, centre - wide / 2 - 0.15, centre + wide / 2 + 0.15, 0.0, 0.1, foot - 0.14, foot, dressed)
    for z, deep in ((TERRACE + 0.6, 0.07), (TERRACE + 6.4, 0.08), (belfry, 0.1)):
        hall.fbox(F, -deep, BELL_SIDE + deep, 0.0, deep, z, z + 0.16, dressed)
    hall.fbox(F, -0.12, BELL_SIDE + 0.12, 0.0, 0.12, TERRACE, TERRACE + 0.6, masonry)
    quoins(hall, F, BELL_SIDE, TERRACE + 0.76, BELL_TOP - 0.5, tall=0.5)
    # The head: corbels carrying a course that oversails the wall.
    hall.fbox(F, -0.2, BELL_SIDE + 0.2, 0.0, 0.2, BELL_TOP - 0.24, BELL_TOP, dressed)
    for k in range(8):
        a = (k + 0.5) * BELL_SIDE / 8
        hall.fprism(F, [(a - 0.09, BELL_TOP - 0.56), (a + 0.09, BELL_TOP - 0.56), (a + 0.09, BELL_TOP - 0.24), (a - 0.09, BELL_TOP - 0.24)], 0.0, 0.16, masonry, sides=[True, True, False, True])
hall.roof(
    tower_faces[0].shifted(-0.2, 0.2), BELL_SIDE + 0.4, BELL_SIDE + 0.4, BELL_TOP, math.radians(64), slate,
    eave=(0.22, 0.22), verge=(0.22, 0.22), hip=(1.0, 1.0), thick=0.09, under=dark_wood, ridge=masonry,
)
tip = BELL_TOP + (BELL_HALF + 0.42) * math.tan(math.radians(64))
hall.cylinder((TOWER_X, tower_y + BELL_HALF), 0.03, tip - 0.3, tip + 1.3, iron, segments=5)
hall.box((TOWER_X - 0.4, tower_y + BELL_HALF - 0.012, tip + 0.85), (TOWER_X + 0.4, tower_y + BELL_HALF + 0.012, tip + 0.95), iron)
hall.box((TOWER_X + 0.25, tower_y + BELL_HALF - 0.012, tip + 0.75), (TOWER_X + 0.4, tower_y + BELL_HALF + 0.012, tip + 1.05), iron)
add("Hall", hall.mesh("Hall"), town, lod_ratio=1.0)


# --- The well ------------------------------------------------------------------
# A ring of stone you can look down into, on a step, with a windlass and
# bucket under a small tiled roof.

well = Builder([masonry])
WELL_OUTER, WELL_INNER, WELL_RIM, WELL_WATER = 1.15, 0.8, 0.9, 0.3
well.cylinder((0, 0), 1.6, -0.2, 0.14, masonry, segments=16)  # the step round it
ring = [(math.cos(math.tau * k / 16), math.sin(math.tau * k / 16)) for k in range(16)]
for (cx, cy), (nx, ny) in zip(ring, ring[1:] + ring[:1]):
    outward = Vector(((cx + nx) / 2, (cy + ny) / 2, 0.0))

    def rim(radius, z, cx=cx, cy=cy, nx=nx, ny=ny):
        return [(cx * radius, cy * radius, z), (nx * radius, ny * radius, z)]

    for inner, outer, low, high, mat in ((WELL_INNER, WELL_OUTER, 0.1, WELL_RIM - 0.12, masonry), (WELL_INNER - 0.04, WELL_OUTER + 0.07, WELL_RIM - 0.12, WELL_RIM, PLASTERS[3])):
        well.poly(rim(outer, low) + rim(outer, high)[::-1], mat, outward)
        well.poly(rim(inner, WELL_WATER if mat is masonry else low) + rim(inner, high)[::-1], mat, -outward)
        well.poly(rim(inner, high) + rim(outer, high)[::-1], mat, UP)
        if mat is not masonry:
            well.poly(rim(inner, low) + rim(outer, low)[::-1], mat, -UP)
well.poly([(x * WELL_INNER, y * WELL_INNER, WELL_WATER) for x, y in ring], water_surface, UP)
for side in (-1, 1):
    x = side * (WELL_OUTER + 0.2)
    well.box((x - 0.09, -0.09, 0.1), (x + 0.09, 0.09, 2.5), dark_wood, grain="long")
    brace = [(x, -0.05, 1.95), (x, -0.05, 1.8), (x - side * 0.55, -0.05, 2.36), (x - side * 0.4, -0.05, 2.36)]
    well.prism(brace, (0, 0.1, 0), dark_wood, back=True, grain=(-side, 0, 1))
    for y in (-1, 1):  # struts out to the eaves
        strut = [(x - 0.05, y * 0.09, 2.0), (x - 0.05, y * 0.09, 1.85), (x - 0.05, y * 0.78, 2.4), (x - 0.05, y * 0.7, 2.47)]
        well.prism(strut, (0.1, 0, 0), dark_wood, back=True, grain=(0, y, 1))
well.box((-WELL_OUTER - 0.45, -0.08, 2.36), (WELL_OUTER + 0.45, 0.08, 2.5), dark_wood, grain="long")
for y in (-0.78, 0.78):
    well.box((-WELL_OUTER - 0.45, y - 0.06, 2.4), (WELL_OUTER + 0.45, y + 0.06, 2.5), dark_wood, grain="long")
# The windlass: a drum between the posts, its crank, the rope, and the bucket hanging over the water.
drum = [(-WELL_OUTER - 0.11, math.cos(math.tau * k / 8) * 0.1, 1.55 + math.sin(math.tau * k / 8) * 0.1) for k in range(8)]
well.prism(drum, (2 * WELL_OUTER + 0.22, 0, 0), wood, back=True, grain=(1, 0, 0))
well.box((-WELL_OUTER - 0.5, -0.02, 1.53), (-WELL_OUTER - 0.1, 0.02, 1.57), iron)
well.box((-WELL_OUTER - 0.5, -0.02, 1.25), (-WELL_OUTER - 0.46, 0.02, 1.57), iron)
well.box((-WELL_OUTER - 0.68, -0.025, 1.23), (-WELL_OUTER - 0.46, 0.025, 1.28), wood)
for k in range(5):
    well.box((0.02 + k * 0.045, -0.105, 1.445), (0.055 + k * 0.045, 0.105, 1.655), wood)  # turns of rope on the drum
well.box((0.2, 0.09, 1.02), (0.225, 0.115, 1.56), wood)
well.cylinder((0.21, 0.1), 0.15, 0.72, 1.0, wood, segments=8, top_radius=0.18)
well.cylinder((0.21, 0.1), 0.185, 0.93, 0.97, iron, segments=8)
well.box((0.2, -0.08, 1.0), (0.22, 0.28, 1.02), iron)
well.roof(
    Frame((-WELL_OUTER - 0.4, -0.78), (1, 0)), 2 * WELL_OUTER + 0.8, 1.56, 2.5, math.radians(42), roof_tiles,
    eave=(0.3, 0.3), verge=(0.24, 0.24), thick=0.06, under=dark_wood, barge=dark_wood, ridge=roof_tiles, tile_ends=CLAY_COLUMNS, verge_tiles=roof_tiles,
)
WELL_AT = (-6.0, 5.0)
add("Well", well.mesh("Well"), town, location=(*WELL_AT, ground(*WELL_AT) + PAVING_LIFT), collision="cylinder", collision_radius=1.45, lod_ratio=1.0)


# --- Kit: things placed many times (linked duplicates -> instanced) ------------

props = collection("Props")
placed_counts = {}


def place(mesh, x, y, rotation_z=0.0, z=None, lift=0.0, scale=1.0, **options):
    """Stands a linked duplicate of `mesh` on the ground at (x, y), or at height `z` if given."""
    number = placed_counts.get(mesh.name, 0)
    placed_counts[mesh.name] = number + 1
    foot = ground(x, y) + lift if z is None else z
    return add(f"{mesh.name}.{number:03d}", mesh, props, location=(x, y, foot), rotation_z=rotation_z, scale=scale, **options)


b = Builder([iron])
b.cylinder((0, 0), 0.12, 0.0, 0.25, iron, segments=8)
b.cylinder((0, 0), 0.055, 0.25, 3.3, iron, segments=6)
b.box((-0.2, -0.2, 3.3), (0.2, 0.2, 3.36), iron)
b.box((-0.15, -0.15, 3.36), (0.15, 0.15, 3.78), lamp_glass)
b.pyramid_roof((-0.2, -0.2), (0.2, 0.2), 3.78, 0.22, iron, overhang=0.04)
lamp_mesh = b.mesh("Lamp")

b = Builder([wood])
b.cylinder((0, 0), 0.36, 0.0, 0.95, wood, segments=10)
for band in (0.15, 0.78):
    b.cylinder((0, 0), 0.375, band, band + 0.06, iron, segments=10)
barrel_mesh = b.mesh("Barrel")

b = Builder([wood])
b.box((-0.45, -0.45, 0.0), (0.45, 0.45, 0.9), wood, tile=0.9)
for edge in (-0.45, 0.39):
    b.box((edge, -0.47, 0.0), (edge + 0.06, 0.47, 0.92), dark_wood)
    b.box((-0.47, edge, 0.0), (0.47, edge + 0.06, 0.92), dark_wood)
crate_mesh = b.mesh("Crate")

b = Builder([wood])
b.box((-0.9, -0.22, 0.42), (0.9, 0.22, 0.5), wood, tile=1.0)
b.box((-0.9, 0.16, 0.5), (0.9, 0.22, 0.95), wood, tile=1.0)
for leg in (-0.8, 0.72):
    b.box((leg, -0.2, 0.0), (leg + 0.08, 0.2, 0.42), dark_wood)
bench_mesh = b.mesh("Bench")

stall_meshes = []
for variant, canvas in enumerate(CANVAS):
    # A market stall: a timber frame, a boarded counter, a canvas roof with a
    # scalloped edge and a cloth hung down the back, and goods laid out. The
    # customer stands on its -Y side.
    b = Builder([wood])
    front, back, half_x = -0.9, 0.85, 1.4
    for x in (-half_x, half_x - 0.1):
        b.box((x, front, 0.0), (x + 0.1, front + 0.1, 2.25), dark_wood, grain="long")
        b.box((x, back - 0.1, 0.0), (x + 0.1, back, 2.75), dark_wood, grain="long")
        rail = [(x + 0.02, front, 2.15), (x + 0.02, back, 2.65), (x + 0.02, back, 2.75), (x + 0.02, front, 2.25)]
        b.prism(rail, (0.06, 0, 0), dark_wood, back=True, grain=(0, 1, 0.28))
        b.box((x + 0.02, front + 0.1, 0.3), (x + 0.08, back - 0.1, 0.38), dark_wood, grain="long")
    b.box((-half_x, front + 0.02, 2.17), (half_x, front + 0.08, 2.25), dark_wood, grain="long")
    b.box((-half_x, back - 0.08, 2.67), (half_x, back - 0.02, 2.75), dark_wood, grain="long")
    b.box((-half_x - 0.06, front - 0.12, 0.84), (half_x + 0.06, back - 0.35, 0.9), wood, grain="long")  # the counter
    b.box((-half_x + 0.1, front + 0.03, 0.22), (half_x - 0.1, front + 0.06, 0.84), wood, grain="long")  # boarded in below it
    b.box((-half_x + 0.1, front + 0.2, 0.3), (half_x - 0.1, back - 0.4, 0.34), wood, grain="long")  # a shelf underneath
    # The canvas: a sheet sloping toward the customer, a scalloped edge along its front, and a cloth down the back.
    stripes = 9
    for k in range(stripes):  # striped: the stall's colour and undyed cloth by turns
        x0, x1 = -half_x - 0.2 + (2 * half_x + 0.4) * k / stripes, -half_x - 0.2 + (2 * half_x + 0.4) * (k + 1) / stripes
        sheet = [(x0, front - 0.35, 2.2), (x1, front - 0.35, 2.2), (x1, back + 0.12, 2.82), (x0, back + 0.12, 2.82)]
        b.prism(sheet, (0, 0.012, -0.027), canvas if k % 2 == 0 else PLASTERS[0], back=True, sides=[True, k == stripes - 1, True, k == 0])
        flap = [(x0, front - 0.35, 2.2), (x1, front - 0.35, 2.2), (x1, front - 0.35, 2.07), ((x0 + x1) / 2, front - 0.35, 2.0), (x0, front - 0.35, 2.07)]
        b.prism(flap, (0, -0.012, 0), canvas if k % 2 == 0 else PLASTERS[0], back=True, sides=False)
    b.box((-half_x + 0.1, back - 0.07, 0.22), (half_x - 0.1, back - 0.04, 1.3), wood, grain="long")  # boarded behind
    hang = [(-half_x, back + 0.02, 2.28), (half_x, back + 0.02, 2.28), (half_x, back + 0.02, 2.74), (-half_x, back + 0.02, 2.74)]
    b.prism(hang, (0, 0.012, 0), canvas, back=True, sides=False)
    for x in (-half_x - 0.01, half_x + 0.01):  # and a triangle of it closing each side under the roof
        b.prism([(x, front + 0.05, 2.24), (x, back, 2.74), (x, back, 1.9)], (0.012 if x > 0 else -0.012, 0, 0), canvas, back=True, sides=False)
    # Goods.
    if variant == 0:  # fruit and greens, in crates tipped toward the customer
        for i, (x, fruit) in enumerate(((-1.25, CANVAS[0]), (-0.38, PLASTERS[4]), (0.5, CANVAS[2]))):
            b.box((x, front, 0.9), (x + 0.75, front + 0.6, 0.94), dark_wood)
            for x0, x1, y0, y1 in ((x, x + 0.75, front, front + 0.03), (x, x + 0.75, front + 0.57, front + 0.6), (x, x + 0.03, front, front + 0.6), (x + 0.72, x + 0.75, front, front + 0.6)):
                b.box((x0, y0, 0.94), (x1, y1, 1.06 + (0.06 if y0 > front + 0.3 else 0.0)), wood, grain="long")
            for row in range(3):
                for col in range(4):
                    before = set(b.bm.faces)
                    ball = bmesh.ops.create_icosphere(b.bm, subdivisions=1, radius=0.075 if i != 1 else 0.085)
                    bmesh.ops.translate(b.bm, vec=(x + 0.12 + col * 0.17, front + 0.12 + row * 0.17, 1.02 + 0.02 * ((row + col) % 2)), verts=ball["verts"])
                    b.finish(set(b.bm.faces) - before, fruit)
    elif variant == 1:  # bolts of cloth
        for i, (x, cloth) in enumerate(((-1.2, CANVAS[0]), (-0.86, CANVAS[2]), (-0.52, canvas), (0.3, PLASTERS[3]), (0.64, CANVAS[0]), (0.98, canvas))):
            bolt = [(x + 0.14 + math.cos(math.tau * k / 8) * 0.14, front + 0.02, 1.04 + math.sin(math.tau * k / 8) * 0.14) for k in range(8)]
            b.prism(bolt, (0, 0.7, 0), cloth, back=True)
        b.box((-0.2, front - 0.05, 0.9), (0.25, front + 0.5, 0.93), canvas)  # a length laid out
    else:  # cheeses, loaves and pots
        for x, y, radius, tall, mat in (
            (-1.0, -0.6, 0.2, 0.12, CANVAS[2]), (-1.0, -0.6, 0.17, 0.24, CANVAS[2]), (-0.5, -0.55, 0.2, 0.12, CANVAS[2]), (-0.72, -0.2, 0.18, 0.11, CANVAS[2]),
            (0.2, -0.55, 0.13, 0.26, roof_tiles), (0.52, -0.6, 0.1, 0.2, roof_tiles), (0.86, -0.5, 0.15, 0.3, roof_tiles), (1.15, -0.62, 0.09, 0.18, roof_tiles),
        ):
            b.cylinder((x, y), radius, 0.9, 0.9 + tall, mat, segments=10, top_radius=radius * (0.7 if mat is roof_tiles else 0.96))
        for x in (0.25, 0.7):
            b.box((x, -0.2, 0.9), (x + 0.36, 0.02, 1.02), wood, grain="long")
    stall_meshes.append(b.mesh(f"Stall{variant}"))

b = Builder([wood])
b.box((-1.5, -0.04, 0.35), (1.5, 0.04, 0.47), wood, tile=1.0)
b.box((-1.5, -0.04, 0.8), (1.5, 0.04, 0.92), wood, tile=1.0)
for post in (-1.5, -0.06, 1.38):
    b.box((post, -0.07, 0.0), (post + 0.12, 0.07, 1.1), dark_wood)
fence_mesh = b.mesh("Fence")

b = Builder([])
hay = material("Hay", (0.48, 0.36, 0.10), roughness=1.0)
b.cylinder((0, 0), 0.75, 0.0, 1.2, hay, segments=12)
bmesh.ops.rotate(b.bm, verts=b.bm.verts[:], cent=(0, 0, 0.6), matrix=Matrix.Rotation(math.pi / 2, 3, "X"))
bmesh.ops.translate(b.bm, verts=b.bm.verts[:], vec=(0, 0, 0.15))
hay_mesh = b.mesh("HayBale")

# Trees and bushes come from plants.py: real wood carrying cut-out leaf cards,
# each with a plain stand-in for the distance. A handful of different ones,
# placed many times as linked duplicates. (`bark` was made above, so they use it.)
bark["detile"] = 1.0
tree_meshes = [
    broadleaf_tree("oak", 0),
    broadleaf_tree("oak", 1),
    broadleaf_tree("ash", 0),
    broadleaf_tree("ash", 1),
    broadleaf_tree("thorn", 0),
    conifer_tree("fir", 0),
]
yard_trees = [broadleaf_tree("thorn", 0), broadleaf_tree("thorn", 1), broadleaf_tree("ash", 1)]
stalk = material("Stalk", (0.07, 0.15, 0.03), roughness=0.85)

# Lamps along the streets and around the square.
for y in np.arange(-WALL + 8, -SQUARE_HALF - 2, 11.0):
    for side in (-1, 1):
        place(lamp_mesh, side * (STREET_HALF - 0.5), float(y), lift=PAVING_LIFT, collision="cylinder", collision_radius=0.12)
for x in np.arange(SQUARE_HALF + 6, WALL - 8, 11.0):
    for side in (-1, 1):
        place(lamp_mesh, side * float(x), LANE_HALF - 0.5, lift=PAVING_LIFT, collision="cylinder", collision_radius=0.12)
for sx in (-1, 1):
    for sy in (-1, 1):
        place(lamp_mesh, sx * (SQUARE_HALF - 1), sy * (SQUARE_HALF - 1), lift=PAVING_LIFT, collision="cylinder", collision_radius=0.12)
for side in (-1, 1):
    place(lamp_mesh, side * 5.2, TERRACE_FRONT + 1.2, z=TERRACE, collision="cylinder", collision_radius=0.12)

# The market: stalls in two rows on the east of the square, benches round the well.
for i, (x, y, turn) in enumerate([(7.0, 6.5, math.pi), (11.5, 6.5, math.pi), (7.0, -1.0, 0.0), (11.5, -1.0, 0.0), (9.0, -9.0, math.pi)]):
    place(stall_meshes[i % 3], x, y, rotation_z=turn, lift=PAVING_LIFT, collision="box")
for angle in (0.4, 2.2, 4.1):
    place(
        bench_mesh,
        WELL_AT[0] + math.cos(angle) * 3.2,
        WELL_AT[1] + math.sin(angle) * 3.2,
        rotation_z=angle + math.pi / 2,
        lift=PAVING_LIFT,
        collision="box",
    )

# Barrels and crates in clusters against walls and by the stalls.
for cx, cy in [(13.0, 2.6), (5.0, 9.5), (-13.0, -12.5), (-6.5, -WALL + 5.0), (8.0, -WALL + 6.0), (-22.0, 5.2), (27.0, -1.9), (12.5, -12.5)]:
    for _ in range(rng.randint(2, 5)):
        x, y = cx + rng.uniform(-1.3, 1.3), cy + rng.uniform(-1.0, 1.0)
        if rng.random() < 0.55:
            place(barrel_mesh, x, y, rotation_z=rng.uniform(0, math.tau), lift=0.02, collision="cylinder", collision_radius=0.38)
        else:
            place(crate_mesh, x, y, rotation_z=rng.uniform(0, math.tau), lift=0.02, collision="box")

# --- Outside the walls ---------------------------------------------------------

# Fences either side of the road, turning with it.
for k in range(8, len(road_points) - 30, 3):
    (ax, ay), (bx, by) = road_points[k - 1], road_points[k + 1]
    along = math.atan2(by - ay, bx - ax)
    for side in (-1, 1):
        fx = road_points[k][0] - math.sin(along) * side * 4.4
        fy = road_points[k][1] + math.cos(along) * side * 4.4
        if min(math.hypot(fx - tx, fy - ty) for tx, ty in (junction, farm_fork)) < 6:
            continue  # a gap where a trail leaves the road
        place(fence_mesh, fx, fy, rotation_z=along, collision="box")

# The farm: a thatched house and a boarded barn on a levelled plot, reached by its own trail.


def barn(b, lo, hi, front):
    """
    A barn on the rectangle lo..hi: weatherboarding on a timber frame over a
    stone plinth, great doors in the middle of its `front` up a ramp, a loft
    hatch under a hoist in one end, a cart shed leaning against the other, and
    a half-hipped roof of thatch.
    """
    x0, y0, x1, y1 = lo[0], lo[1], hi[0], hi[1]
    under = [ground(x, y) for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)]
    base = max(under) + 0.1
    sill = base + 0.45
    top = sill + 3.5
    bare_rect((x0, y0), (x1, y1), margin=0.6)
    footing(b, x0, y0, x1, y1, min(under) - 0.6, sill)
    if front == "n":
        FF, W, D = Frame((x1, y1), (-1, 0)), x1 - x0, y1 - y0
    elif front == "s":
        FF, W, D = Frame((x0, y0), (1, 0)), x1 - x0, y1 - y0
    elif front == "e":
        FF, W, D = Frame((x1, y0), (0, 1)), y1 - y0, x1 - x0
    else:
        FF, W, D = Frame((x0, y1), (0, -1)), y1 - y0, x1 - x0
    walls = {"front": (FF, W), "right": (FF.right(W, 0.0), D), "back": (FF.reverse(W, -D), W), "left": (FF.left(0.0, -D), D)}
    door_half, door_top = 1.5, sill + 3.0
    for name, (F, length) in walls.items():
        openings = []
        if name == "front":
            openings.append((W / 2 - door_half, W / 2 + door_half, sill, door_top, 0.14, wood))
        elif name == "back":
            for a in (length * 0.25, length * 0.75):  # slits to air the hay
                openings.append((a - 0.09, a + 0.09, sill + 1.5, sill + 2.5, 0.2, dark_wood))
        b.wall(F, length, sill, top, wood, openings, lining=dark_wood)
        # The frame: rails at foot, waist and head, posts, and a long brace in each end bay.
        bays = max(2, round(length / 2.3))
        for z0, z1 in ((sill, sill + 0.2), (sill + 1.75, sill + 1.9), (top - 0.2, top)):
            at = 0.0
            for g0, g1 in sorted((o[0], o[1]) for o in openings if o[2] < z1 and o[3] > z0) + [(length, length)]:
                if g0 > at:
                    b.fbox(F, at, g0, 0.0, 0.06, z0, z1, dark_wood)
                at = max(at, g1)
        for k in range(1, bays):
            a = length * k / bays
            if not any(o[0] - 0.1 < a < o[1] + 0.1 for o in openings):
                b.fbox(F, a - 0.1, a + 0.1, 0.0, 0.066, sill + 0.2, top - 0.2, dark_wood)
        for a_foot, a_head in ((0.3, length / bays - 0.25), (length - 0.3, length - length / bays + 0.25)):
            run = math.hypot(a_head - a_foot, 1.55)
            w = 0.075 * run / 1.55
            b.fprism(F, [(a_foot - w, sill + 0.2), (a_foot + w, sill + 0.2), (a_head + w, sill + 1.75), (a_head - w, sill + 1.75)], 0.0, 0.05, dark_wood, grain=F.U * (a_head - a_foot) + UP * 1.55)
    for a0, a1 in ((-0.07, 0.2), (W - 0.2, W + 0.07)):
        for d0, d1 in ((-0.2, 0.07), (-D - 0.07, -D + 0.2)):
            b.box(*sorted_box(FF.at(a0, d0, sill), FF.at(a1, d1, top)), dark_wood, grain=(0, 0, 1))
    # The great doors: two leaves of upright boards, ledged and braced, on long iron hinges, in a heavy frame.
    DF = FF.shifted(0.0, -0.14)
    for side in (-1, 1):
        edge, meet = W / 2 + side * door_half, W / 2 + side * 0.02
        lo_a, hi_a = min(edge, meet), max(edge, meet)
        for z in (sill + 0.3, sill + 1.45, door_top - 0.45):
            b.fbox(DF, lo_a + 0.05, hi_a - 0.05, 0.0, 0.035, z, z + 0.16, dark_wood)
            b.fbox(DF, *sorted((edge, edge - side * 0.75)), 0.035, 0.05, z + 0.05, z + 0.11, iron)
        for z0, z1 in ((sill + 0.46, sill + 1.45), (sill + 1.61, door_top - 0.45)):
            foot, head = meet + side * 0.12, edge - side * 0.12
            b.fprism(DF, [(foot - 0.09, z0), (foot + 0.09, z0), (head + 0.09, z1), (head - 0.09, z1)], 0.0, 0.03, dark_wood, grain=DF.U * (head - foot) + UP * (z1 - z0))
        b.fbox(FF, *sorted((edge, edge + side * 0.2)), -0.02, 0.09, sill, door_top, dark_wood)
    b.fbox(FF, W / 2 - door_half - 0.35, W / 2 + door_half + 0.35, -0.02, 0.11, door_top, door_top + 0.26, dark_wood)
    foot = ground(*FF.at(W / 2, 2.4, 0.0)[:2])
    ramp = [FF.at(W / 2 - door_half - 0.3, 0.0, foot - 0.5), FF.at(W / 2 - door_half - 0.3, 2.6, foot - 0.5), FF.at(W / 2 - door_half - 0.3, 2.6, foot - 0.05), FF.at(W / 2 - door_half - 0.3, 0.0, sill)]
    b.prism(ramp, FF.U * (2 * door_half + 0.6), masonry, back=True)

    # The roof: thatch, laid thick, half-hipped at both ends.
    pitch = math.radians(50)
    tan = math.tan(pitch)
    eave, verge, hip, thick = 0.6, 0.45, 0.45, 0.32
    crest, surface = b.roof(
        FF, W, D, top, pitch, thatch, eave=(eave, eave), verge=(verge, verge), hip=(hip, hip), thick=thick,
        under=dark_wood, barge=dark_wood, tails=dark_wood, ridge=thatch, course=2.1, ridge_size=(0.55, 0.16, 1.3),
    )
    cap = top + (D / 2 - hip * (D / 2 + eave) + verge) * tan
    for e, G in enumerate((FF.left(0.0, -D), FF.right(W, 0.0))):
        gable_end(b, G, D, top, pitch, wood, infill="studs", cap=cap)
        if e == 1:
            # The loft hatch, shut, and the beam and hook that lift sacks up to it.
            b.fbox(G, D / 2 - 0.5, D / 2 + 0.5, 0.0, 0.06, top + 0.14, top + 1.3, wood, grain=UP)
            for a0, a1, z0, z1 in ((-0.62, -0.5, 0.02, 1.42), (0.5, 0.62, 0.02, 1.42), (-0.62, 0.62, 1.3, 1.42), (-0.62, 0.62, 0.02, 0.14)):
                b.fbox(G, D / 2 + a0, D / 2 + a1, 0.0, 0.085, top + z0, top + z1, dark_wood)
            for z in (0.35, 1.0):
                b.fbox(G, D / 2 - 0.5, D / 2 + 0.3, 0.06, 0.075, top + z, top + z + 0.06, iron)
            b.fbox(G, D / 2 - 0.08, D / 2 + 0.08, 0.0, 1.15, cap - 0.42, cap - 0.26, dark_wood, grain=G.N)
            b.fbox(G, D / 2 - 0.015, D / 2 + 0.015, 0.95, 0.98, cap - 0.75, cap - 0.42, iron)
            b.fbox(G, D / 2 - 0.05, D / 2 + 0.05, 0.92, 1.01, cap - 0.8, cap - 0.75, iron)
    # A cart shed leaning against the left-hand end: posts, an open front, and a roof of old tiles under the barn's eaves.
    S = FF.left(0.0, -D)  # runs from the barn's back corner to its front one
    shed_deep, shed_from, shed_to, shed_high, shed_low = 2.8, 0.5, D - 0.5, sill + 2.75, sill + 1.75
    floor = min(ground(*S.at(a, d, 0.0)[:2]) for a in (shed_from, shed_to) for d in (0.3, shed_deep)) - 0.5
    corners = sorted_box(S.at(shed_from, 0.0, 0.0), S.at(shed_to, shed_deep, 0.0))
    bare_rect(corners[0][:2], corners[1][:2], margin=0.4)
    b.fbox(S, shed_from + 0.03, shed_to, shed_deep - 0.3, shed_deep, floor, sill - 0.15, masonry, back=True)
    outer = S.shifted(shed_from, shed_deep)  # its long wall, boarded, away from the barn
    b.wall(outer, shed_to - shed_from, sill - 0.15, shed_low, wood)
    b.poly([S.at(shed_from, shed_deep - 0.06, sill - 0.15), S.at(shed_to, shed_deep - 0.06, sill - 0.15), S.at(shed_to, shed_deep - 0.06, shed_low), S.at(shed_from, shed_deep - 0.06, shed_low)], wood, -S.N)
    for a, facing in ((shed_from, -S.U), (shed_from + 0.06, S.U)):  # and its far end; the end by the yard is open
        b.poly([S.at(a, 0.0, floor), S.at(a, shed_deep, floor), S.at(a, shed_deep, shed_low), S.at(a, 0.0, shed_high - 0.15)], wood, facing)
    for a in (shed_from, (shed_from + shed_to) / 2, shed_to):
        b.fbox(S, a - 0.09, a + 0.09, shed_deep - 0.18, shed_deep + 0.03, floor, shed_low, dark_wood, back=True)
        b.fbox(S, a - 0.09, a + 0.09, 0.0, 0.18, sill, shed_high - 0.15, dark_wood)
        beam = [S.at(a - 0.07, 0.0, shed_high - 0.15), S.at(a - 0.07, shed_deep + 0.2, shed_low - 0.07), S.at(a - 0.07, shed_deep + 0.2, shed_low - 0.21), S.at(a - 0.07, 0.0, shed_high - 0.29)]
        b.prism(beam, S.U * 0.14, dark_wood, back=True, grain=beam[1] - beam[0])
    b.fbox(S, shed_from - 0.1, shed_to + 0.1, shed_deep - 0.16, shed_deep + 0.03, shed_low - 0.16, shed_low, dark_wood, back=True)
    slope = [S.at(shed_from - 0.3, 0.0, shed_high), S.at(shed_to + 0.3, 0.0, shed_high), S.at(shed_to + 0.3, shed_deep + 0.4, shed_low - 0.07), S.at(shed_from - 0.3, shed_deep + 0.4, shed_low - 0.07)]
    b.prism(slope, UP * -0.08, roof_mossy, front=False, back=True, grain=S.U)
    b.poly([p - UP * 0.08 for p in slope], dark_wood, -UP, grain=S.N)
    b.fbox(S, shed_from - 0.3, shed_to + 0.3, 0.0, 0.1, shed_high - 0.02, shed_high + 0.08, dark_wood)  # where it meets the barn


farm = Builder([])
house(
    farm, (FARM[0] - 9, FARM[1] - 1), (FARM[0] - 1, FARM[1] + 6), 2, PLASTERS[3], "e", chimney=True,
    style={"roof": "eaves", "roofing": thatch, "hip": True, "bay": False, "dormers": 0, "jetty": 0.38, "ground": "stone", "infill": "brace", "stack": "side", "shop": False, "sign": False, "pipe": False, "lamp": True, "pitch": 50},
)
barn(farm, (FARM[0] + 1, FARM[1] - 10), (FARM[0] + 10, FARM[1] - 3), "n")
add("Farm", farm.mesh("Farm"), town, lod_ratio=1.0)
for i in range(7):
    angle = 0.5 + i * 0.42
    place(hay_mesh, FARM[0] + math.cos(angle) * 12, FARM[1] + math.sin(angle) * 12, rotation_z=rng.uniform(0, math.tau), collision="cylinder", collision_radius=0.8)


def scatter(meshes, wanted, accept, scale=(0.8, 1.3), attempts=40000, **options):
    """Places up to `wanted` copies at random spots that `accept(x, y)` approves and where grass could grow."""
    done = 0
    for _ in range(attempts):
        if done >= wanted:
            break
        x, y = rng.uniform(-SIZE / 2 + 8, SIZE / 2 - 8), rng.uniform(-SIZE / 2 + 8, SIZE / 2 - 8)
        if not grows_at(x, y) or not accept(x, y):
            continue
        size = rng.uniform(*scale)
        chosen = meshes[rng.randrange(len(meshes))]
        extra = dict(options)
        if hasattr(chosen, "far"):  # a plant from plants.py: its mesh, its stand-in, and a trunk to walk into
            extra["far_mesh"] = chosen.far.name
            if extra.get("collision") == "cylinder":
                extra["collision_radius"] = round(max(chosen.trunk_radius, 0.12) * size, 3)
            chosen = chosen.mesh
        place(chosen, x, y, rotation_z=rng.uniform(0, math.tau), scale=size, lift=-0.08 * size, **extra)
        done += 1
    return done


def outside(x, y):
    return max(abs(x), abs(y)) > MOAT_OUTER + 5


def near_water(x, y, reach=7.0):
    return any(radius < math.hypot(x - cx, y - cy) < radius + reach for cx, cy, radius, _level in ponds)


def near_path(x, y):
    return abs(x - float(road_x(y))) < 9 or min(math.hypot(x - tx, y - ty) for tx, ty in pond_trail[::4] + farm_trail[::4]) < 4


# Trees in copses, standing back from the road and the farm.
copses = fbm(X * 2.3 + 40, Y * 2.3 - 70, octaves=3)
scatter(
    tree_meshes,
    300,
    lambda x, y: outside(x, y)
    and not near_path(x, y)
    and math.hypot(x - FARM[0], y - FARM[1]) > 18
    and sample(slope, x, y) < 0.4
    and sample(copses, x, y) > rng.uniform(0.45, 0.62),
    scale=(0.85, 1.2),
    collision="cylinder",
)
# A few trees in the town's yards too.
for x, y in [(-40.0, 40.0), (-34.0, 24.0), (38.0, 36.0), (41.0, -8.0), (-41.0, -30.0), (24.0, 41.0), (18.0, -36.0)]:
    tree = yard_trees[rng.randrange(len(yard_trees))]
    size = rng.uniform(0.75, 0.95)
    place(tree.mesh, x, y, rotation_z=rng.uniform(0, math.tau), scale=size, lift=-0.1, collision="cylinder", collision_radius=round(tree.trunk_radius * size, 3), far_mesh=tree.far.name)

# Shrubs: rounded clumps of leaf, in three shapes and three greens.
bush_meshes = [bush("round", 0), bush("tall", 0), bush("low", 0), bush("round", 1)]

# Flowers. Real ones are scans (`npm run model:get -- flower_gazania
# flower_ursinia dandelion_01 flower_empodium`): low rosettes 20 to 30 cm
# across, several plants to a file, of which the ones in bloom are used. The
# heads-on-stalks built below are what the town falls back on without them.
wild_flowers = []
for model, picks, size in (("flower_gazania", (3, 5, 6, 7), 1.5), ("flower_ursinia", (0, 1, 2, 3), 1.5), ("dandelion_01", (2, 3, 4), 1.7), ("flower_empodium", (0, 1, 2), 1.5)):
    try:
        for variant in picks:
            if variant < model_variants(model):
                wild_flowers.append((import_model(model, max_triangles=700, variant=variant).mesh, size))
    except FileNotFoundError:
        pass
flower_meshes = []
for variant, petal in enumerate([CANVAS[0], CANVAS[2], material("PetalWhite", (0.85, 0.85, 0.8), roughness=0.8)]):
    b = Builder([])
    for _ in range(7):
        fx, fy, tall = rng.uniform(-0.4, 0.4), rng.uniform(-0.4, 0.4), rng.uniform(0.18, 0.34)
        b.box((fx - 0.008, fy - 0.008, 0.0), (fx + 0.008, fy + 0.008, tall), stalk)
        before = set(b.bm.faces)
        head = bmesh.ops.create_icosphere(b.bm, subdivisions=1, radius=0.045)
        bmesh.ops.translate(b.bm, vec=(fx, fy, tall), verts=head["verts"])
        b.finish(set(b.bm.faces) - before, petal)
    flower_meshes.append(b.mesh(f"Flowers{variant}"))

# Boulders, in real rock.
rock = surface("Rock", "rock_face_03") or material("Rock", (0.17, 0.165, 0.155), roughness=0.92)
if "metres" in rock:
    rock["detile"] = 1.0
rock_meshes = []
for variant in range(3):
    b = Builder([])
    before = set(b.bm.faces)
    ball = bmesh.ops.create_icosphere(b.bm, subdivisions=2, radius=1.0)
    squash = (rng.uniform(0.8, 1.3), rng.uniform(0.8, 1.3), rng.uniform(0.5, 0.8))
    for vertex in ball["verts"]:
        lump = 0.75 + 0.5 * float(value_noise(np.array(vertex.co.x * 1.3 + variant * 9.1), np.array(vertex.co.y * 1.3 + vertex.co.z * 2.7)))
        vertex.co = Vector((vertex.co.x * squash[0], vertex.co.y * squash[1], vertex.co.z * squash[2])) * lump
    b.finish(set(b.bm.faces) - before, rock)
    rock_meshes.append(b.mesh(f"Boulder{variant}", smooth=True))

scatter(bush_meshes, 150, lambda x, y: outside(x, y) and (sample(copses, x, y) > 0.5 or near_water(x, y)), collision="none")
scatter(bush_meshes, 60, lambda x, y: outside(x, y) and 4.5 < abs(x - float(road_x(y))) < 9 and y < -MOAT_OUTER, collision="none")
scatter(bush_meshes, 45, lambda x, y: max(abs(x), abs(y)) < WALL - 2 and (max(abs(x), abs(y)) > WALL - 9 or near_water(x, y, 5.0)), collision="none")


def flower_patches(wanted, accept, attempts=20000):
    """Flowers grow in patches of one kind: a few plants close together, the odd one of another kind among them."""
    done = 0
    for _ in range(attempts):
        if done >= wanted:
            break
        x, y = rng.uniform(-SIZE / 2 + 8, SIZE / 2 - 8), rng.uniform(-SIZE / 2 + 8, SIZE / 2 - 8)
        if not grows_at(x, y) or not accept(x, y):
            continue
        kind = rng.randrange(len(wild_flowers))
        for _plant in range(rng.randint(5, 12)):
            px, py = x + rng.gauss(0.0, 0.6), y + rng.gauss(0.0, 0.6)
            if not grows_at(px, py):
                continue
            mesh, size = wild_flowers[kind if rng.random() < 0.85 else rng.randrange(len(wild_flowers))]
            place(mesh, px, py, rotation_z=rng.uniform(0, math.tau), scale=size * rng.uniform(0.75, 1.3), lift=-0.02, collision="none", shadows=False, lod_skip=True)
        done += 1


if wild_flowers:
    flower_patches(70, lambda x, y: outside(x, y) and (near_water(x, y, 10.0) or sample(copses, x, y) < 0.42))
    flower_patches(30, lambda x, y: outside(x, y) and near_path(x, y))
    flower_patches(16, lambda x, y: max(abs(x), abs(y)) < WALL - 2)
else:
    scatter(flower_meshes, 160, lambda x, y: outside(x, y) and (near_water(x, y, 10.0) or sample(copses, x, y) < 0.42), scale=(0.8, 1.4), collision="none")
    scatter(flower_meshes, 40, lambda x, y: max(abs(x), abs(y)) < WALL - 2, scale=(0.8, 1.3), collision="none")

# Scanned ferns, shrubs and weeds where nobody mows: under the copses, by the
# water, along the foot of the town wall and in the yards. They are small, so
# they are drawn only near the camera (`lod_skip`) and cast no shadows.
weeds = []
for model, size in (("fern_02", 1.5), ("shrub_03", 2.0), ("shrub_sorrel_01", 2.8), ("weed_plant_02", 2.8)):
    try:
        for variant in range(min(3, model_variants(model))):
            weeds.append((import_model(model, max_triangles=450, variant=variant).mesh, size))
    except FileNotFoundError:
        pass  # not fetched (`npm run model:get -- <id>`): the town does without
for mesh, size in weeds:
    wild = dict(scale=(size * 0.7, size * 1.4), collision="none", shadows=False, lod_skip=True)
    scatter([mesh], 70, lambda x, y: outside(x, y) and (sample(copses, x, y) > 0.47 or near_water(x, y, 9.0)), **wild)
    scatter([mesh], 22, lambda x, y: max(abs(x), abs(y)) < WALL - 1.5 and (max(abs(x), abs(y)) > WALL - 6 or near_water(x, y, 4.0)), **wild)
    scatter([mesh], 16, lambda x, y: MOAT_OUTER + 1 < max(abs(x), abs(y)) < MOAT_OUTER + 9, **wild)
for size_range, wanted, shape in (((1.1, 2.4), 45, "convex"), ((0.3, 0.8), 90, "none")):
    scatter(
        rock_meshes,
        wanted,
        lambda x, y: outside(x, y) and not near_path(x, y) and (sample(slope, x, y) > 0.22 or near_water(x, y, 4.0)),
        scale=size_range,
        collision=shape,
    )
scatter(rock_meshes, 8, lambda x, y: max(abs(x), abs(y)) < WALL - 2 and near_water(x, y, 3.0), scale=(0.3, 0.7), collision="none")


# --- Dressing the houses -------------------------------------------------------
# The fittings the houses asked for as they were built (`dress`): window frames,
# doors, shutters, flower boxes, signs, lamps, awnings, drainpipes and chimney
# pots. Each is made once, small, and placed as linked duplicates, so the game
# draws every window frame in town as one batch. They are modelled standing on
# a wall that runs along +X and faces -Y, their origin on the wall's face.

FRAME = 0.045  # the width of a window's own frame, inside the opening


def window_frame(kind, lights, shelf=False):
    """The woodwork of a window: a surround on the wall's face, a frame in the opening, mullions and a transom. Only the faces that show."""
    wide, tall = WINDOWS[kind]
    h = wide / 2
    b = Builder([dark_wood])

    def bar(x0, x1, z0, z1, y0, y1, shown):
        """A length of timber from depth y0 (nearer) to y1; `shown`: which of its bottom, right, top and left sides to make."""
        b.prism([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], (0, y0 - y1, 0), dark_wood, sides=shown, grain=(1, 0, 0) if x1 - x0 > z1 - z0 else (0, 0, 1))

    bar(-h - 0.09, -h, 0.0, tall, -0.055, 0.0, [False, True, False, True])  # jambs
    bar(h, h + 0.09, 0.0, tall, -0.055, 0.0, [False, True, False, True])
    bar(-h - 0.14, h + 0.14, tall, tall + 0.11, -0.068, 0.0, True)  # head
    bar(-h - 0.16, h + 0.16, -0.09, 0.0, -0.125, 0.0, True)  # sill
    bar(-h, -h + FRAME, 0.0, tall, 0.03, 0.09, [False, True, False, False])  # the frame in the opening
    bar(h - FRAME, h, 0.0, tall, 0.03, 0.09, [False, False, False, True])
    bar(-h + FRAME, h - FRAME, tall - FRAME, tall, 0.03, 0.09, [True, False, False, False])
    bar(-h + FRAME, h - FRAME, 0.0, FRAME, 0.03, 0.09, [False, False, True, False])
    for k in range(1, lights):
        x = -h + wide * k / lights
        bar(x - 0.024, x + 0.024, FRAME, tall - FRAME, 0.035, 0.085, [False, True, False, True])
    if tall > 1.0:
        bar(-h + FRAME, h - FRAME, tall * 0.64 - 0.02, tall * 0.64 + 0.02, 0.035, 0.085, [True, False, True, False])
    if shelf:  # a shop's counter board, let down on two brackets
        b.box((-h - 0.1, -0.42, -0.06), (h + 0.1, -0.1, -0.015), wood, grain="long")
        for x in (-h + 0.2, h - 0.2):
            b.prism([(x - 0.03, 0.0, -0.45), (x - 0.03, -0.36, -0.06), (x - 0.03, 0.0, -0.06)], (0.06, 0, 0), dark_wood, back=True)
    return b.mesh("Window" + kind)


def lit_pane(kind):
    """Lamplight behind a window: a glowing pane just in front of the dark glass."""
    wide, tall = WINDOWS[kind]
    h = wide / 2 - FRAME
    b = Builder([window_lit])
    b.poly([(-h, 0.1, FRAME), (h, 0.1, FRAME), (h, 0.1, tall - FRAME), (-h, 0.1, tall - FRAME)], window_lit, (0, -1, 0))
    return b.mesh("Lit" + kind)


def shutter(name, paint):
    """One leaf of a pair of shutters, folded back flat against the wall: upright boards, two ledges and their strap hinges."""
    b = Builder([paint])
    b.prism([(-0.22, -0.056, 0.0), (0.22, -0.056, 0.0), (0.22, -0.056, 1.2), (-0.22, -0.056, 1.2)], (0, -0.034, 0), paint, grain=(0, 0, 1))
    for z in (0.18, 0.94):
        b.prism([(-0.21, -0.09, z), (0.21, -0.09, z), (0.21, -0.09, z + 0.09), (-0.21, -0.09, z + 0.09)], (0, -0.015, 0), paint, sides=[True, False, True, False], grain=(1, 0, 0))
        b.prism([(-0.23, -0.105, z + 0.03), (0.1, -0.105, z + 0.03), (0.1, -0.105, z + 0.06), (-0.23, -0.105, z + 0.06)], (0, -0.007, 0), iron, sides=[True, False, True, False])
    return b.mesh("Shutter" + name)


def door(name, face, glazed):
    """A door for the 1.1 x 2.1 m opening, set back in it: planked and strapped, or painted and panelled with a small light."""
    b = Builder([face])
    b.box((-0.55, 0.1, 0.0), (0.55, 0.16, 2.1), face, grain=(0, 0, 1))
    if glazed:
        for x0, x1 in ((-0.55, -0.43), (0.43, 0.55), (-0.06, 0.06)):
            b.box((x0, 0.074, 0.0), (x1, 0.1, 2.1), face, grain=(0, 0, 1))  # stiles
        for z0, z1 in ((0.0, 0.2), (0.95, 1.1), (1.38, 1.48), (1.82, 1.94), (1.98, 2.1)):
            b.box((-0.43, 0.084, z0), (0.43, 0.1, z1), face, grain="long")  # rails
        b.box((-0.43, 0.092, 1.48), (0.43, 0.1, 1.82), glass)
        for x in (-0.3, -0.18, 0.18, 0.3):
            b.box((x - 0.008, 0.084, 1.48), (x + 0.008, 0.092, 1.82), iron)
    else:
        for z in (0.32, 1.02, 1.72):
            b.box((-0.53, 0.086, z), (0.25, 0.1, z + 0.07), iron)  # strap hinges
            for x in (-0.45, -0.2, 0.05):
                b.box((x, 0.078, z + 0.02), (x + 0.03, 0.086, z + 0.05), iron)
    b.box((0.36, 0.06, 0.95), (0.39, 0.075, 1.2), iron)  # a pull
    for z in (0.96, 1.16):
        b.box((0.36, 0.075, z), (0.39, 0.1, z + 0.03), iron)
    b.box((0.31, 0.092, 0.9), (0.44, 0.1, 1.25), iron)
    return b.mesh("Door" + name)


def flower_box(variant, petal):
    b = Builder([wood])
    b.box((-0.5, -0.33, -0.31), (0.5, -0.135, -0.11), wood, grain="long")
    b.box((-0.47, -0.305, -0.13), (0.47, -0.16, -0.1), dark_wood)  # earth
    for x in (-0.36, 0.36):
        b.prism([(x - 0.025, 0.0, -0.5), (x - 0.025, -0.3, -0.31), (x - 0.025, 0.0, -0.31)], (0.05, 0, 0), dark_wood, back=True)
    spots = rng
    for k in range(4):
        before = set(b.bm.faces)
        ball = bmesh.ops.create_icosphere(b.bm, subdivisions=1, radius=spots.uniform(0.1, 0.13))
        bmesh.ops.translate(b.bm, vec=(-0.36 + 0.24 * k, -0.235 + spots.uniform(-0.03, 0.03), -0.04 + spots.uniform(-0.02, 0.03)), verts=ball["verts"])
        b.finish(set(b.bm.faces) - before, stalk)
    for k in range(6):
        before = set(b.bm.faces)
        head = bmesh.ops.create_icosphere(b.bm, subdivisions=1, radius=0.045)
        bmesh.ops.translate(b.bm, vec=(-0.4 + 0.16 * k, -0.26 + spots.uniform(-0.07, 0.05), 0.05 + spots.uniform(-0.04, 0.05)), verts=head["verts"])
        b.finish(set(b.bm.faces) - before, petal)
    return b.mesh(f"FlowerBox{variant}")


def hanging_sign(variant, paint):
    """A painted board hung from an iron bracket, edge-on to the wall."""
    b = Builder([iron])
    b.box((-0.018, -1.02, -0.018), (0.018, 0.0, 0.018), iron)
    b.prism([(-0.012, 0.0, -0.46), (-0.012, -0.56, -0.03), (-0.012, -0.56, 0.0), (-0.012, 0.0, -0.42)], (0.024, 0, 0), iron, back=True)
    b.box((-0.05, -0.02, -0.5), (0.05, 0.0, 0.06), iron)  # the plate on the wall
    for y in (-0.42, -0.9):
        b.box((-0.008, y - 0.012, -0.13), (0.008, y + 0.012, -0.018), iron)
    b.box((-0.025, -1.0, -0.72), (0.025, -0.32, -0.13), wood, grain="long")
    b.box((-0.032, -0.95, -0.67), (0.032, -0.37, -0.18), paint, grain="long")
    emblem = [(-0.04, -0.66, -0.56), (-0.04, -0.52, -0.425), (-0.04, -0.66, -0.29), (-0.04, -0.8, -0.425)]
    b.prism(emblem, (0.08, 0, 0), CANVAS[2], back=True)
    return b.mesh(f"Sign{variant}")


def wall_lamp():
    b = Builder([iron])
    b.box((-0.014, -0.42, 0.0), (0.014, 0.0, 0.028), iron)
    b.prism([(-0.01, 0.0, -0.3), (-0.01, -0.32, -0.02), (-0.01, -0.32, 0.0), (-0.01, 0.0, -0.27)], (0.02, 0, 0), iron, back=True)
    b.box((-0.04, -0.015, -0.34), (0.04, 0.0, 0.06), iron)
    b.box((-0.006, -0.386, -0.07), (0.006, -0.374, 0.0), iron)
    b.pyramid_roof((-0.1, -0.48), (0.1, -0.28), -0.1, 0.09, iron, overhang=0.03)
    b.box((-0.085, -0.465, -0.34), (0.085, -0.295, -0.1), lamp_glass)
    b.box((-0.1, -0.48, -0.37), (0.1, -0.28, -0.34), iron)
    for x in (-0.09, 0.08):
        for y in (-0.47, -0.3):
            b.box((x, y, -0.34), (x + 0.012, y + 0.012, -0.1), iron)
    return b.mesh("WallLamp")


def awning(variant, canvas):
    """Canvas over a shop window: a sloping sheet on two iron rods, with a scalloped edge hanging from its front."""
    b = Builder([canvas])
    half, reach, fall = 1.2, 1.25, 0.5
    stripes = 9
    for k in range(stripes):  # striped: its colour and undyed cloth by turns
        cloth = canvas if k % 2 == 0 else PLASTERS[0]
        x0, x1 = -half + 2 * half * k / stripes, -half + 2 * half * (k + 1) / stripes
        sheet = [(x0, 0.0, 0.0), (x1, 0.0, 0.0), (x1, -reach, -fall), (x0, -reach, -fall)]
        b.prism(sheet, Vector((0, -fall, reach)).normalized() * -0.03, cloth, back=True, sides=[False, k == stripes - 1, True, k == 0])
        flap = [(x0, -reach, -fall), (x1, -reach, -fall), (x1, -reach, -fall - 0.13), ((x0 + x1) / 2, -reach, -fall - 0.2), (x0, -reach, -fall - 0.13)]
        b.prism(flap, (0, -0.012, 0), cloth, back=True, sides=False)
    for x in (-half + 0.02, half - 0.02):
        b.box((x - 0.012, -reach, -fall - 0.03), (x + 0.012, 0.0, -fall - 0.006), iron)
        b.box((x - 0.012, -0.012, -fall - 0.03), (x + 0.012, 0.0, 0.0), iron)
    return b.mesh(f"Awning{variant}")


def drainpipe():
    """Three metres of pipe (stretched to fit when placed) from a hopper under the gutter down to the ground."""
    b = Builder([iron])
    b.cylinder((0.0, -0.09), 0.045, 0.0, 2.9, iron, segments=6)
    for z in (0.5, 1.7, 2.7):
        b.box((-0.075, -0.15, z), (0.075, 0.0, z + 0.03), iron)
    b.box((-0.09, -0.17, 2.86), (0.09, -0.02, 3.0), iron)
    b.box((-0.04, -0.6, 2.95), (0.04, -0.12, 3.02), iron)
    return b.mesh("Drainpipe")


def chimney_pot():
    b = Builder([roof_tiles])
    b.cylinder((0, 0), 0.17, -0.02, 0.06, roof_tiles, segments=8)
    b.cylinder((0, 0), 0.145, 0.06, 0.5, roof_tiles, segments=8, top_radius=0.105)
    b.cylinder((0, 0), 0.13, 0.5, 0.55, roof_tiles, segments=8)
    return b.mesh("ChimneyPot")


petal_white = bpy.data.materials["PetalWhite"]
fittings = {
    "WindowA": window_frame("A", 2),
    "WindowB": window_frame("B", 3),
    "WindowC": window_frame("C", 2),
    "WindowS": window_frame("S", 4, shelf=True),
    "LitA": lit_pane("A"),
    "LitB": lit_pane("B"),
    "ShutterGreen": shutter("Green", paint_green),
    "ShutterRed": shutter("Red", paint_red),
    "ShutterOak": shutter("Oak", wood),
    "DoorOak": door("Oak", wood, glazed=False),
    "DoorGreen": door("Green", paint_green, glazed=True),
    "DoorRed": door("Red", paint_red, glazed=True),
    "FlowerBox0": flower_box(0, CANVAS[0]),
    "FlowerBox1": flower_box(1, petal_white),
    "Sign0": hanging_sign(0, paint_green),
    "Sign1": hanging_sign(1, paint_red),
    "WallLamp": wall_lamp(),
    "Awning0": awning(0, CANVAS[0]),
    "Awning1": awning(1, CANVAS[1]),
    "Awning2": awning(2, CANVAS[2]),
    "Drainpipe": drainpipe(),
    "ChimneyPot": chimney_pot(),
}
FLAT_ON_THE_WALL = ("Window", "Lit", "Shutter", "Door", "FlowerBox")  # too slight to be worth a shadow
dressing_triangles = 0
for kind, x, y, z, turn, options in dressing:
    name = kind + str(options["variant"]) if "variant" in options else kind + str(options["colour"]) if "colour" in options else kind
    mesh = fittings[name]
    scale = (1.0, 1.0, options["tall"] / 3.0) if "tall" in options else 1.0
    extra = {"shadows": False} if kind.startswith(FLAT_ON_THE_WALL) else {}
    place(mesh, x, y, rotation_z=turn, z=z, scale=scale, collision="none", **extra)
    dressing_triangles += sum(len(p.vertices) - 2 for p in mesh.polygons)


# --- Markers: what the game's code is told about --------------------------------

markers = collection("Markers")


def marker(name, x, y, z=None, facing=0.0, display="SINGLE_ARROW", size=1.0, scale=(1, 1, 1), **options):
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = display
    empty.empty_display_size = size
    empty.location = (x, y, sample(height, x, y) + 0.2 if z is None else z)
    empty.rotation_euler = (0, 0, facing)
    empty.scale = scale
    markers.objects.link(empty)
    for key, value in options.items():
        empty[key] = value


def zone(name, lo, hi):
    """A trigger zone covering the box lo..hi: a cube Empty scaled to fit."""
    centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
    marker(name, centre[0], centre[1], z=centre[2], display="CUBE", scale=[(hi[i] - lo[i]) / 2 for i in range(3)], trigger=name)


# Named points. An Empty's -Y is "forward", so facing north takes half a turn.
spawn_at = road_points[42]
toward_town = road_points[30]
marker("spawn", *spawn_at, facing=math.atan2(toward_town[0] - spawn_at[0], -(toward_town[1] - spawn_at[1])))
# A door to another level: where the road leaves for the coast. Going through
# it replaces the town with the island (it is a world, not an interior).
away = Vector((spawn_at[0] - toward_town[0], spawn_at[1] - toward_town[1])).normalized() * 7.0
marker("road_to_coast", spawn_at[0] + away.x, spawn_at[1] + away.y, door="island#spawn_pier", prompt="Take the road to the coast", radius=3.0)
marker("poi_square", 0.0, -SQUARE_HALF + 3.0, facing=math.pi)
marker("poi_hall", 0.0, TERRACE_FRONT + 2.0, z=TERRACE + 0.2, facing=math.pi)
marker("poi_wall_walk", stair_top_x - 5.0, -WALL, z=WALL_HEIGHT + 0.2, facing=math.pi / 2)
marker("poi_pond", pond_x - pond_radius - 3, pond_y + 2, facing=-math.pi / 2)
marker("poi_farm", FARM[0] + 12, FARM[1] + 4, facing=math.pi / 2)
marker("poi_viewpoint", *VIEWPOINT, facing=math.pi)
marker("poi_duck_pond", INNER_POND[0] - INNER_POND[2] - 2, INNER_POND[1], facing=-math.pi / 2)
marker("poi_stairs", stair_foot_x + 0.6, stair_y + 3.0, z=0.2, facing=-math.pi / 2)

# Trigger zones.
zone("town_gate", (-GATE_HALF, -WALL - 2.0, 0.0), (GATE_HALF, -WALL + 2.0, 4.2))
zone("market_square", (-SQUARE_HALF, -SQUARE_HALF, 0.0), (SQUARE_HALF, SQUARE_HALF, 4.0))
zone("wall_walk", (-corner, -WALL - half, WALL_HEIGHT), (-(gate_tower_x + GATE_TOWER), -WALL + half, WALL_HEIGHT + 3.0))
zone("hall_terrace", (-TERRACE_HALF, TERRACE_FRONT, TERRACE), (TERRACE_HALF, HALL_FRONT, TERRACE + 3.5))
# One name, four zones: the moat on each side of the town.
moat_mid, moat_half = (MOAT_INNER + MOAT_OUTER) / 2, (MOAT_OUTER - MOAT_INNER) / 2 - 2.5
for i, (cx, cy, wx, wy) in enumerate(
    [(0, -moat_mid, MOAT_OUTER, moat_half), (0, moat_mid, MOAT_OUTER, moat_half), (-moat_mid, 0, moat_half, MOAT_INNER), (moat_mid, 0, moat_half, MOAT_INNER)]
):
    marker(f"moat_{'snwe'[i]}", cx, cy, z=WATER_LEVEL - 1.0, display="CUBE", scale=(wx, wy, 1.0), trigger="moat")

# Things to use. What each does is the game's business (src/game/interactions.ts).
marker("well", *WELL_AT, z=1.0, interact="well", prompt="Draw water", radius=2.6)
# A door: it leads to the marker `entrance` in the space `hall` (the hall's
# inside, its own map: tools/blender/make_town_hall.py). See src/engine/spaces.ts.
marker("hall_door", 0.0, HALL_FRONT - 0.4, z=TERRACE + 1.2, door="hall#entrance", prompt="Enter the hall", radius=2.4)
# The gate: the game builds a portcullis here and works it from the lever.
marker("portcullis", 0.0, -WALL, z=0.0, display="CUBE", scale=(GATE_HALF, 0.15, 2.2), kind="portcullis")
marker("gate_lever", GATE_HALF + 1.3, -WALL + 2.7, z=1.0, interact="gate_lever", prompt="Lower the gate", radius=2.2)

# Effects: two braziers by the hall steps that can be put out, and smoke from some chimneys.
b = Builder([iron])
b.cylinder((0, 0), 0.07, 0.0, 0.8, iron, segments=6)
b.cylinder((0, 0), 0.2, 0.8, 1.0, iron, segments=8, top_radius=0.42)
brazier_mesh = b.mesh("Brazier")
for i, side in enumerate((-1, 1)):
    bx, by = side * 6.2, STAIR_FOOT_Y - 0.8
    place(brazier_mesh, bx, by, lift=PAVING_LIFT, collision="cylinder", collision_radius=0.3)
    marker(f"brazier_{i}", bx, by, z=ground(bx, by) + 1.05, fx="campfire", fx_scale=0.7, interact="campfire", prompt="Put out the fire", radius=2.0)
for i, (cx, cy, cz) in enumerate(rng.sample(chimney_tops, min(4, len(chimney_tops)))):
    marker(f"chimney_{i}", cx, cy, z=cz + 0.1, fx="smoke", fx_scale=1.4)


# --- Terrain object ------------------------------------------------------------
# Last, now that everything standing on the land has cleared its patch of grass.
# The colour attribute tints the ground texture (lush, dry, trodden, mud).

# The ground set is a dark forest floor. It is recoloured to a pale tone, at
# least as bright as any colour wanted of it, and the painted colours then
# tint that down to each: meadow green, dry straw, trodden earth, mud.
GROUND_TONE = np.array([0.25, 0.26, 0.12])
ground_surface = surface("Ground", "grass_ground", vertex_colors="Col", tone=tuple(GROUND_TONE))
if ground_surface:
    ground_surface["detile"] = 1.0  # organic: let the game shuffle its repeats (src/engine/surface.ts)
    dirt["detile"] = 1.0
# Tints multiply the texture; without the texture they are the colours themselves.
shade = 1 / GROUND_TONE if ground_surface else np.array([1.0, 1.0, 1.0])
LUSH = np.array([0.105, 0.20, 0.045])
DRY = np.array([0.24, 0.25, 0.08])
TRODDEN = np.array([0.19, 0.165, 0.105])
MUD = np.array([0.10, 0.085, 0.06])

patches = fbm(X * 3.1, Y * 3.1, octaves=3)
dry = smoothstep(0.35, 0.7, patches)
color = lerp_color(np.broadcast_to(LUSH, (count, count, 3)), DRY, dry)
color = lerp_color(color, TRODDEN, 0.75 * (1 - smoothstep(WALL - 2, WALL + 2, square)))  # trodden earth inside the walls
# Along the paths the grass is trampled for a pace or two either side, and far
# away (where the blend isn't drawn) this tint is all that shows of them.
EARTH = np.array([0.21, 0.16, 0.10])
trampled = np.zeros((count, count))
for points, half in paths:
    nearest = np.full((count, count), 1e9)
    for px, py in points[::2]:
        nearest = np.minimum(nearest, np.hypot(X - px, Y - py))
    trampled = np.maximum(trampled, smoothstep(half + 2.2, half * 0.3, nearest))
color = lerp_color(color, EARTH, 0.8 * trampled)
wet = np.maximum(moat, np.max([smoothstep(r * 1.05, r * 0.7, np.hypot(X - cx, Y - cy)) for cx, cy, r, _level in ponds], axis=0))
color = lerp_color(color, MUD, wet)
cover = cover * (1 - 0.45 * (1 - smoothstep(WALL - 2, WALL + 2, square))) * (1 - 0.4 * dry) * (1 - smoothstep(0.55, 0.9, slope))
rgba = np.concatenate([np.clip(color * shade, 0, 1), np.ones((count, count, 1))], axis=2)

terrain_mesh = bpy.data.meshes.new("Terrain")
index = np.arange(count * count).reshape(count, count)
quads = np.stack(
    [index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, 1:].ravel(), index[1:, :-1].ravel()], axis=1
)
terrain_mesh.from_pydata(np.stack([X.ravel(), Y.ravel(), height.ravel()], axis=1).tolist(), [], quads.tolist())
terrain_mesh.update()
terrain_mesh.shade_smooth()
uv_layer = terrain_mesh.uv_layers.new(name="UVMap")
corner_vertex = np.empty(len(terrain_mesh.loops), dtype=np.int32)
terrain_mesh.loops.foreach_get("vertex_index", corner_vertex)
metres = ground_surface.get("metres", 2.5) if ground_surface else 2.5
uv_layer.data.foreach_set("uv", (np.stack([X.ravel(), Y.ravel()], axis=1)[corner_vertex] / metres).astype(np.float32).ravel())
paint = terrain_mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
paint.data.foreach_set("color", rgba.reshape(-1, 4).astype(np.float32).ravel())
terrain_mesh.color_attributes.active_color = paint
terrain_mesh.color_attributes.render_color_index = 0
terrain_mesh.materials.append(ground_surface or material("Terrain", (1, 1, 1), roughness=0.95, vertex_colors=True))
# `ground_cover` asks the game to plant grass on it. Where, and how thickly, is
# a vertex group of the same name: what you would paint in Weight Paint mode.
terrain = add("Terrain", terrain_mesh, world, collision="mesh", ground_cover="grass", height_map=True)
# The paths: earth blended into the grass wherever the mask is white.
mask_image = bpy.data.images.new("PathsMask", MASK_SIZE, MASK_SIZE, alpha=False)
mask_image.colorspace_settings.name = "Non-Color"
mask_image.pixels.foreach_set(np.repeat(path_mask[..., None], 4, axis=2).astype(np.float32).ravel())
mask_image.pack()
mask_image.use_fake_user = True  # nothing in the file uses it, and Blender doesn't save what nothing uses
dirt.use_fake_user = True  # likewise: no object is made of it
terrain["blend_layer"] = dirt.name
terrain["blend_mask"] = mask_image.name
growth = terrain.vertex_groups.new(name="ground_cover")
for vertex, weight in enumerate(np.clip(cover, 0, 1).ravel()):
    if weight > 0.01:
        growth.add([vertex], float(weight), "REPLACE")

# --- Save --------------------------------------------------------------------

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
if not argv:
    raise SystemExit("usage: blender -b --factory-startup --python make_test_town.py -- <output.blend>")
path = os.path.abspath(argv[0])
os.makedirs(os.path.dirname(path), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=path)
triangles = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in town.objects)
print(
    f"[make_test_town] {path}: {len(town.objects)} town objects ({triangles:,} triangles), "
    f"{len(props.objects)} placed props from {len(placed_counts)} meshes "
    f"({len(dressing)} of them fittings on the houses, {dressing_triangles:,} triangles), {len(markers.objects)} markers"
)
for o in town.objects:
    print(f"[make_test_town]   {o.name}: {sum(len(p.vertices) - 2 for p in o.data.polygons):,} triangles, {len(o.data.materials)} materials")
