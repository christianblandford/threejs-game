"""
The building kit shared by the scripts that make maps out of code: the
helpers of `make_test_town.py` (which is a script and cannot be imported),
lifted out unchanged so that another script builds data the exporter already
handles.

    import kit
    from kit import Builder, Frame, UP, add, collection, marker, material

    kit.reset()                      # an empty scene
    b = Builder([stone])
    b.box((0, 0, 0), (4, 0.4, 3), stone)
    add("Wall", b.mesh("Wall"), collection("Building"))
    marker("spawn", 0.0, -2.0, 0.2, facing=math.pi)

What is here:

- `material`, `collection`, `add`: plain Blender data the exporter reads.
- `Frame`: a wall to build on, in (along, out, height).
- `Builder`: collects boxes, prisms, walls with openings and roofs into one
  mesh with several materials, with UVs in metres so every texture is at its
  true scale. (`rod` and the arguments of `stairs` are the only additions.)
- `pointed_arch`, `lancet_top`, `block`: small shapes the stone buildings use.
- `marker`, `zone`: Empties for the game's code (an Empty's -Y is "forward").
- `Placer`: linked duplicates, which the game draws as instances.
- `value_noise`, `fbm`, `smoothstep`: for painting variation by position.

`make_test_town.py` keeps its own copies; a change to one is not a change to
the other.
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector

RISER, TREAD = 0.15, 0.3  # every stair: low enough to walk up without jumping, deep enough to path over


# --- Noise -------------------------------------------------------------------

LATTICE = np.random.default_rng(11).random((256, 256))


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


# --- Blender helpers ---------------------------------------------------------


def reset():
    """Empties the scene Blender starts with."""
    for datablock in list(bpy.data.objects):
        bpy.data.objects.remove(datablock)


def collection(name):
    result = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(result)
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

    def rod(self, start, end, radius, mat, segments=6, end_radius=None, caps=True, tile=None):
        """
        A round bar between two points, whichever way it lies: a pole, a
        chain, a log, a table leg. (Not in make_test_town.py, whose cylinders
        all stand upright.)
        """
        start, end = Vector(start), Vector(end)
        axis = end - start
        if axis.length < 1e-6:
            return
        before = set(self.bm.faces)
        made = bmesh.ops.create_cone(
            self.bm, cap_ends=caps, segments=segments, radius1=radius, radius2=radius if end_radius is None else end_radius, depth=axis.length
        )
        turn = Vector((0.0, 0.0, 1.0)).rotation_difference(axis.normalized()).to_matrix()
        bmesh.ops.rotate(self.bm, verts=made["verts"], cent=(0.0, 0.0, 0.0), matrix=turn)
        bmesh.ops.translate(self.bm, vec=(start + end) / 2, verts=made["verts"])
        self.finish(set(self.bm.faces) - before, mat, tile, grain=axis.normalized())

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

    def stairs(self, start, direction, steps, width, mat, riser=RISER, tread=TREAD, below=1.2):
        """
        A solid flight of `steps` risers from `start` (the foot's centre, on the
        ground) climbing along `direction` (a unit X or Y vector).
        """
        dx, dy = direction
        px, py = -dy, dx  # across the flight
        for i in range(steps):
            near = (start[0] + dx * tread * i, start[1] + dy * tread * i)
            far = (start[0] + dx * tread * (i + 1), start[1] + dy * tread * (i + 1))
            xs = [near[0] - px * width / 2, near[0] + px * width / 2, far[0] - px * width / 2, far[0] + px * width / 2]
            ys = [near[1] - py * width / 2, near[1] + py * width / 2, far[1] - py * width / 2, far[1] + py * width / 2]
            self.box((min(xs), min(ys), start[2] - below), (max(xs), max(ys), start[2] + riser * (i + 1)), mat)

    def mesh(self, name, smooth=False):
        for face in self.bm.faces:
            face.smooth = smooth
        result = bpy.data.meshes.new(name)
        self.bm.to_mesh(result)
        self.bm.free()
        for mat in self.materials:
            result.materials.append(mat)
        return result


# --- Shapes ------------------------------------------------------------------


def block(builder, p, q, mat):
    """A box between two opposite corners, given in any order."""
    builder.box(tuple(min(p[i], q[i]) for i in range(3)), tuple(max(p[i], q[i]) for i in range(3)), mat)


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


def lancet_top(wide, spring):
    return spring + wide * math.sin(math.radians(60))


# --- Markers: what the game's code is told about ------------------------------

_markers = None


def marker(name, x, y, z, facing=0.0, display="SINGLE_ARROW", size=1.0, scale=(1, 1, 1), where=None, **options):
    """
    An Empty the game finds by name, with `options` as its custom properties
    (`fx`, `interact`, `trigger`, ...). An Empty's -Y is "forward", so
    `facing` 0 looks south, pi north, pi / 2 east and -pi / 2 west. They go in
    a collection called Markers unless `where` says otherwise.
    """
    if where is None:
        where = markers()
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = display
    empty.empty_display_size = size
    empty.location = (x, y, z)
    empty.rotation_euler = (0, 0, facing)
    empty.scale = scale
    where.objects.link(empty)
    for key, value in options.items():
        empty[key] = value
    return empty


def markers():
    """The collection `marker` puts its Empties in, made on first use."""
    global _markers
    if _markers is None or _markers.name not in bpy.data.collections:
        _markers = collection("Markers")
    return _markers


def zone(name, lo, hi, where=None):
    """A trigger zone covering the box lo..hi: a cube Empty scaled to fit."""
    centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
    return marker(name, centre[0], centre[1], centre[2], display="CUBE", scale=[(hi[i] - lo[i]) / 2 for i in range(3)], where=where, trigger=name)


# --- Things placed many times (linked duplicates -> instanced) ----------------


class Placer:
    """
    Stands linked duplicates of meshes in a collection, numbering them. The
    exporter keeps one copy of each mesh and the game draws every placing of
    it in one batch, so they are static: nothing that must move, hide or be
    picked on its own should be placed this way.
    """

    def __init__(self, where):
        self.where = where
        self.counts = {}

    def __call__(self, mesh, x, y, z=0.0, rotation_z=0.0, scale=1.0, **options):
        number = self.counts.get(mesh.name, 0)
        self.counts[mesh.name] = number + 1
        return add(f"{mesh.name}.{number:03d}", mesh, self.where, location=(x, y, z), rotation_z=rotation_z, scale=scale, **options)

    def triangles(self):
        return sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in self.where.objects)
