"""
Builds the scaffold's test map -- a small island -- and saves it as a .blend.

    blender -b --factory-startup --python tools/blender/make_test_map.py -- maps/test_island.blend

(or `npm run map:make-test`). Open the result in Blender to look around or
edit it, then export it with `npm run map:export -- maps/test_island.blend`.

Everything here is ordinary Blender data made the way you would by hand: one
terrain mesh painted with a colour attribute, trees and rocks as linked
duplicates, a few unique meshes, a water plane and some empties as markers.
The stonework and the tree bark use texture sets from maps/textures/ (see
tools/blender/surfaces.py). If those haven't been fetched, textures painted by
this script stand in, so it always produces a textured map.
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
from surfaces import surface  # noqa: E402
from plants import broadleaf_tree, bush, conifer_tree, import_model, model_variants  # noqa: E402

SIZE = 768.0  # metres across
STEP = 2.0  # terrain grid spacing
WATER_LEVEL = -2.0
PLATEAU_RADIUS = 30.0  # flat ground around the origin for the demo props
SEED = 7

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


def fbm(x, y, octaves=5, frequency=1 / 170, gain=0.5):
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

count = int(SIZE / STEP) + 1
axis = np.linspace(-SIZE / 2, SIZE / 2, count)
X, Y = np.meshgrid(axis, axis, indexing="xy")
distance = np.sqrt(X * X + Y * Y)

island = smoothstep(1.0, 0.5, distance / (SIZE / 2))  # 1 inland, 0 at the map edge
hills = fbm(X, Y)
ridges = 1 - np.abs(2 * fbm(X + 400, Y - 250, octaves=4, frequency=1 / 120) - 1)
height = ((hills - 0.36) * 62 + ridges * ridges * 14) * island - (1 - island) * 11
flat = smoothstep(PLATEAU_RADIUS * 4.5, PLATEAU_RADIUS, distance)  # 1 on the plateau, easing out to the hills
height = height * (1 - flat)

grad_y, grad_x = np.gradient(height, STEP)
slope = np.sqrt(grad_x**2 + grad_y**2)


def sample(grid, x, y):
    """Bilinear lookup of a terrain grid at a world position."""
    fx = min(max((x + SIZE / 2) / STEP, 0), count - 1.001)
    fy = min(max((y + SIZE / 2) / STEP, 0), count - 1.001)
    ix, iy = int(fx), int(fy)
    tx, ty = fx - ix, fy - iy
    top = grid[iy, ix] * (1 - tx) + grid[iy, ix + 1] * tx
    bottom = grid[iy + 1, ix] * (1 - tx) + grid[iy + 1, ix + 1] * tx
    return float(top * (1 - ty) + bottom * ty)


def lerp_color(a, b, t):
    return a + (b - a) * t[..., None]


SAND = np.array([0.56, 0.47, 0.27])
WET = np.array([0.20, 0.19, 0.13])
GRASS = np.array([0.09, 0.19, 0.035])
DRY = np.array([0.24, 0.25, 0.07])
ROCK = np.array([0.20, 0.185, 0.17])
PEAK = np.array([0.42, 0.41, 0.40])

patches = fbm(X * 3.1, Y * 3.1, octaves=3)
color = lerp_color(np.broadcast_to(GRASS, (count, count, 3)), DRY, smoothstep(0.35, 0.7, patches))
color = lerp_color(color, SAND, 1 - smoothstep(WATER_LEVEL + 0.5, WATER_LEVEL + 1.5, height))
color = lerp_color(color, WET, 1 - smoothstep(WATER_LEVEL - 1.5, WATER_LEVEL + 0.4, height))
color = lerp_color(color, ROCK, smoothstep(0.45, 0.8, slope))
color = lerp_color(color, PEAK, smoothstep(30, 40, height) * (1 - smoothstep(0.5, 0.9, slope)))
# Where grass grows: above the beach, off the steep ground and the high peaks,
# and not on the clearing at the origin where the game's demo props stand. It
# becomes the terrain's `ground_cover` vertex group (see the terrain object).
cover = (
    smoothstep(WATER_LEVEL + 1.4, WATER_LEVEL + 2.6, height)
    * (1 - smoothstep(0.4, 0.65, slope))
    * (1 - smoothstep(26, 34, height))
    * smoothstep(17.0, 24.0, distance)
    * (1 - 0.35 * smoothstep(0.35, 0.7, patches))
)
rgba = np.concatenate([color, np.ones((count, count, 1))], axis=2)


# --- Blender helpers ---------------------------------------------------------

for datablock in list(bpy.data.objects):
    bpy.data.objects.remove(datablock)
scene = bpy.context.scene


def collection(name):
    result = bpy.data.collections.new(name)
    scene.collection.children.link(result)
    return result


def material(name, color, roughness=0.9, metallic=0.0, vertex_colors=False, texture=None, normal_map=None):
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


def mesh_from(bm, name, materials, smooth=False):
    for face in bm.faces:
        face.smooth = smooth
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for mat in materials:
        mesh.materials.append(mat)
    return mesh


def box(bm, size, centre):
    result = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=result["verts"])
    bmesh.ops.translate(bm, vec=centre, verts=result["verts"])


def box_uv(bm, metres_per_tile):
    """UVs by projecting every face along the axis it faces, so a texture is the same size on all of them."""
    layer = bm.loops.layers.uv.verify()
    bm.normal_update()
    for face in bm.faces:
        facing = max(range(3), key=lambda i: abs(face.normal[i]))
        across, up = [i for i in range(3) if i != facing]
        for loop in face.loops:
            loop[layer].uv = (loop.vert.co[across] / metres_per_tile, loop.vert.co[up] / metres_per_tile)


# --- Textures ----------------------------------------------------------------
# Painted here instead of loaded from disk, so the script needs no other files.
# They tile, and they are packed into the .blend. Rows run bottom to top, as
# Blender stores them.

TEXTURE_SIZE = 512
texture_rng = np.random.default_rng(SEED + 1)  # its own sequence, so the island keeps its shape


def tiling_noise(cells_x, cells_y, octaves=4):
    """Fractal value noise over the texture square that repeats seamlessly at its edges."""
    total, amplitude, norm = np.zeros((TEXTURE_SIZE, TEXTURE_SIZE)), 1.0, 0.0
    for _ in range(octaves):
        lattice = texture_rng.random((cells_y, cells_x))
        x, y = np.meshgrid(
            np.arange(TEXTURE_SIZE) * cells_x / TEXTURE_SIZE, np.arange(TEXTURE_SIZE) * cells_y / TEXTURE_SIZE
        )
        xi, yi = x.astype(int), y.astype(int)
        u, v = x - xi, y - yi
        u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
        x1, y1 = (xi + 1) % cells_x, (yi + 1) % cells_y
        a, b, c, d = lattice[yi, xi], lattice[yi, x1], lattice[y1, xi], lattice[y1, x1]
        total += amplitude * (a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v)
        norm += amplitude
        amplitude *= 0.5
        cells_x, cells_y = cells_x * 2, cells_y * 2
    return total / norm


def image(name, pixels, color=True):
    """A packed 8-bit image from a (height, width, 3) array. Colours are given in linear RGB."""
    pixels = np.clip(pixels, 0.0, 1.0)
    if color:  # 8-bit colour images are stored display-encoded
        pixels = np.where(pixels <= 0.0031308, pixels * 12.92, 1.055 * pixels ** (1 / 2.4) - 0.055)
    height, width = pixels.shape[:2]
    result = bpy.data.images.new(name, width, height, alpha=False)
    if not color:
        result.colorspace_settings.name = "Non-Color"
    rgba = np.concatenate([pixels, np.ones((height, width, 1))], axis=2)
    result.pixels.foreach_set(rgba.astype(np.float32).ravel())
    result.pack()
    return result


def normal_image(name, heights, strength):
    """A tangent-space normal map (+Y up, as Blender and glTF expect) from a tiling height field."""
    slope_u = (np.roll(heights, -1, axis=1) - np.roll(heights, 1, axis=1)) * 0.5 * strength
    slope_v = (np.roll(heights, -1, axis=0) - np.roll(heights, 1, axis=0)) * 0.5 * strength
    normal = np.stack([-slope_u, -slope_v, np.ones_like(heights)], axis=2)
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    return image(name, normal * 0.5 + 0.5, color=False)


# Stone blocks: four courses of two blocks per tile, every other course shifted
# half a block, with recessed mortar joints.
COURSES, BLOCKS = 4, 2
u, v = np.meshgrid((np.arange(TEXTURE_SIZE) + 0.5) / TEXTURE_SIZE, (np.arange(TEXTURE_SIZE) + 0.5) / TEXTURE_SIZE)
course = np.floor(v * COURSES).astype(int)
along = u * BLOCKS + 0.5 * (course % 2)
block = np.floor(along).astype(int) % BLOCKS
to_joint = np.minimum((0.5 - np.abs(along % 1 - 0.5)) / BLOCKS, (0.5 - np.abs(v * COURSES % 1 - 0.5)) / COURSES)
on_block = smoothstep(0.004, 0.012, to_joint)  # 0 in the mortar, 1 on a block
grain = tiling_noise(6, 6, octaves=6)
tint = texture_rng.uniform(0.8, 1.15, (COURSES, BLOCKS))[course, block]
block_color = np.array([0.38, 0.348, 0.295]) * (tint * (0.55 + 0.9 * grain))[..., None]
mortar_color = np.broadcast_to([0.16, 0.15, 0.135], block_color.shape)
masonry_color = image("MasonryColor", lerp_color(mortar_color, block_color, on_block))
masonry_normal = normal_image("MasonryNormal", smoothstep(0.0, 0.03, to_joint) + 0.45 * grain, strength=14.0)

# Bark: noise stretched along the trunk, darkest in the furrows.
ridges = tiling_noise(24, 3, octaves=5)
furrows = smoothstep(0.35, 0.6, ridges)
bark_color = image("BarkColor", np.array([0.12, 0.07, 0.036]) * (0.35 + 0.85 * furrows * (0.7 + 0.6 * ridges))[..., None])


# --- Terrain object ----------------------------------------------------------

terrain_mesh = bpy.data.meshes.new("Terrain")
index = np.arange(count * count).reshape(count, count)
quads = np.stack(
    [index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, 1:].ravel(), index[1:, :-1].ravel()], axis=1
)
# Deep sea floor is hidden under opaque water: don't model what can't be seen.
flat_height = height.ravel()
quads = quads[flat_height[quads].max(axis=1) > WATER_LEVEL - 4.0]
used = np.unique(quads)
remap = np.full(count * count, -1)
remap[used] = np.arange(len(used))
quads = remap[quads]
positions = np.stack([X.ravel(), Y.ravel(), flat_height], axis=1)[used]
rgba = rgba.reshape(-1, 4)[used]
terrain_mesh.from_pydata(positions.tolist(), [], quads.tolist())
terrain_mesh.update()
terrain_mesh.shade_smooth()

# The ground: one photographed texture, recoloured pale so that the painted
# colours can tint it down to sand, grass, wet shore and bare rock alike, and
# shuffled by the game so it never shows its repeat. (Without the texture set
# the painted colours are used as they are.)
GROUND_TONE = np.array([0.56, 0.47, 0.40])
ground = surface("Ground", "grass_ground", vertex_colors="Col", tone=tuple(GROUND_TONE))
if ground:
    ground["detile"] = 1.0
    rgba[:, :3] = np.clip(rgba[:, :3] / GROUND_TONE, 0, 1)
    corner_vertex = np.empty(len(terrain_mesh.loops), dtype=np.int32)
    terrain_mesh.loops.foreach_get("vertex_index", corner_vertex)
    uv_layer = terrain_mesh.uv_layers.new(name="UVMap")
    uv_layer.data.foreach_set("uv", (positions[corner_vertex, :2] / ground["metres"]).astype(np.float32).ravel())
paint = terrain_mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
paint.data.foreach_set("color", rgba.astype(np.float32).ravel())
terrain_mesh.color_attributes.active_color = paint
terrain_mesh.color_attributes.render_color_index = 0
terrain_mesh.materials.append(ground or material("Terrain", (1, 1, 1), roughness=0.95, vertex_colors=True))

world = collection("World")
terrain = add("Terrain", terrain_mesh, world, collision="mesh", ground_cover="grass", height_map=True)
growth = terrain.vertex_groups.new(name="ground_cover")
for vertex, weight in enumerate(np.clip(cover.ravel()[used], 0, 1)):
    if weight > 0.01:
        growth.add([vertex], float(weight), "REPLACE")

# Cliffs: real rock blended into the ground wherever it is steep, through a
# mask image laid over the terrain (white = rock). The game does the blending
# (`blend_layer` in export_map.py), so the rock's edge breaks up naturally.
cliff = surface("Cliff", "rock_face_03")
if ground and cliff:
    cliff["detile"] = 1.0
    cliff.use_fake_user = True  # no object is made of it, and Blender doesn't save what nothing uses
    MASK_SIZE = 1024
    low, high = positions.min(axis=0), positions.max(axis=0)  # the mask spans the terrain's bounding box
    mask_x = low[0] + (np.arange(MASK_SIZE) + 0.5) / MASK_SIZE * (high[0] - low[0])
    mask_y = low[1] + (np.arange(MASK_SIZE) + 0.5) / MASK_SIZE * (high[1] - low[1])

    def resample(grid, xs, ys):
        """The terrain grid read at every pixel of the mask (bilinear)."""
        fx = np.clip((xs + SIZE / 2) / STEP, 0, count - 1.001)
        fy = np.clip((ys + SIZE / 2) / STEP, 0, count - 1.001)
        ix, iy = fx.astype(int), fy.astype(int)
        tx, ty = (fx - ix)[None, :], (fy - iy)[:, None]
        top = grid[np.ix_(iy, ix)] * (1 - tx) + grid[np.ix_(iy, ix + 1)] * tx
        bottom = grid[np.ix_(iy + 1, ix)] * (1 - tx) + grid[np.ix_(iy + 1, ix + 1)] * tx
        return top * (1 - ty) + bottom * ty

    rocky = smoothstep(0.42, 0.75, resample(slope, mask_x, mask_y))
    rocky = np.maximum(rocky, smoothstep(31, 38, resample(height, mask_x, mask_y)) * 0.85)  # and the bare tops
    mask_image = bpy.data.images.new("CliffMask", MASK_SIZE, MASK_SIZE, alpha=False)
    mask_image.colorspace_settings.name = "Non-Color"
    mask_image.pixels.foreach_set(np.repeat(rocky[..., None], 4, axis=2).astype(np.float32).ravel())
    mask_image.pack()
    mask_image.use_fake_user = True
    terrain["blend_layer"] = cliff.name
    terrain["blend_mask"] = mask_image.name

# --- Water (never unloads) ---------------------------------------------------

water = bmesh.new()
bmesh.ops.create_grid(water, x_segments=1, y_segments=1, size=1400)
add(
    "Water",
    # The game swaps its own water in for materials named "Water" (clear) or "Sea" (opaque: the floor isn't modelled out here).
    mesh_from(water, "Water", [material("Sea", (0.012, 0.075, 0.10), roughness=0.06)]),
    world,
    location=(0, 0, WATER_LEVEL),
    always_loaded=True,
    collision="none",
    shadows=False,
)

# --- Trees (linked duplicates -> instanced in the game) ----------------------

# A real texture set if it has been fetched (`npm run texture:get -- bark_brown_02`), else the painted one.
bark = surface("Bark", "bark_brown_02") or material("Bark", (0.10, 0.058, 0.03), roughness=0.95, texture=bark_color)
bark["detile"] = 1.0
# Mostly conifers, as on a northern island, with broadleaves on the lower
# ground. A handful of different trees, each placed many times as linked
# duplicates: the game draws each kind in one batch, and far away shows the
# plain stand-in that plants.py makes alongside.
conifers = [conifer_tree("spruce", 0), conifer_tree("spruce", 1), conifer_tree("fir", 0), conifer_tree("fir", 1)]
broadleaves = [broadleaf_tree("oak", 0), broadleaf_tree("ash", 0), broadleaf_tree("thorn", 0)]

trees = collection("Trees")
forest = fbm(X * 1.7 + 90, Y * 1.7 - 40, octaves=3)
tree_spots = []
placed = 0
for _ in range(20000):
    if placed >= 900:
        break
    x, y = rng.uniform(-SIZE / 2, SIZE / 2), rng.uniform(-SIZE / 2, SIZE / 2)
    z = sample(height, x, y)
    if not (WATER_LEVEL + 2.2 < z < 30) or sample(slope, x, y) > 0.45 or math.hypot(x, y) < PLATEAU_RADIUS + 6:
        continue
    if sample(forest, x, y) < rng.uniform(0.42, 0.6):
        continue
    if any((x - tx) ** 2 + (y - ty) ** 2 < 9.0 for tx, ty in tree_spots[-60:]):
        continue  # trunks don't grow out of each other
    low = z < rng.uniform(4.0, 12.0)  # broadleaves keep to the lower ground
    tree = rng.choice(broadleaves) if low and rng.random() < 0.7 else rng.choice(conifers)
    scale = rng.uniform(0.8, 1.2)
    add(
        f"Tree.{placed:03d}",
        tree.mesh,
        trees,
        location=(x, y, z - 0.2),
        rotation_z=rng.uniform(0, math.tau),
        scale=scale,
        collision="cylinder",
        collision_radius=round(tree.trunk_radius * scale, 3),
        far_mesh=tree.far.name,
    )
    tree_spots.append((x, y))
    placed += 1

# Undergrowth: bushes at the wood's edge, and scanned ferns and shrubs (small,
# so drawn only near the camera: `lod_skip`) scattered under and around the trees.
undergrowth = collection("Undergrowth")
bushes = [bush("round", 0), bush("tall", 0), bush("low", 0), bush("round", 1)]
ground_plants = []
for model, picks, size in (
    ("fern_02", (0, 1, 2), 1.6),
    ("shrub_03", (0, 1, 2), 2.2),
    ("shrub_sorrel_01", (0, 1, 2), 3.0),
    ("weed_plant_02", (0, 1, 2), 3.0),
):
    try:
        for variant in picks:
            if variant < model_variants(model):
                ground_plants.append((import_model(model, max_triangles=600, variant=variant), size))
    except FileNotFoundError:
        pass  # not fetched (`npm run model:get -- <id>`): the island does without
# Flowers: each file holds several plants, of which these are the ones in bloom.
flowers = []
for model, picks, size in (("flower_gazania", (5, 6, 7), 1.5), ("flower_ursinia", (0, 1, 2), 1.5), ("dandelion_01", (2, 3), 1.7)):
    try:
        for variant in picks:
            if variant < model_variants(model):
                flowers.append((import_model(model, max_triangles=600, variant=variant), size))
    except FileNotFoundError:
        pass
placed = 0
for _ in range(60000):
    if placed >= 2600:
        break
    x, y = rng.uniform(-SIZE / 2, SIZE / 2), rng.uniform(-SIZE / 2, SIZE / 2)
    z = sample(height, x, y)
    if not (WATER_LEVEL + 1.8 < z < 30) or sample(slope, x, y) > 0.5 or math.hypot(x, y) < PLATEAU_RADIUS + 3:
        continue
    wooded = sample(forest, x, y)
    if wooded < rng.uniform(0.36, 0.55):
        continue  # the same pattern as the trees, reaching a little further out
    if ground_plants and rng.random() < 0.8:
        plant, size = rng.choice(ground_plants)
        add(
            f"Plant.{placed:04d}",
            plant.mesh,
            undergrowth,
            location=(x, y, z - 0.03),
            rotation_z=rng.uniform(0, math.tau),
            scale=size * rng.uniform(0.7, 1.4),
            collision="none",
            shadows=False,
            lod_skip=True,
        )
    else:
        shrub = rng.choice(bushes)
        add(
            f"Bush.{placed:04d}",
            shrub.mesh,
            undergrowth,
            location=(x, y, z - 0.08),
            rotation_z=rng.uniform(0, math.tau),
            scale=rng.uniform(0.8, 1.5),
            collision="none",
            far_mesh=shrub.far.name,
        )
    placed += 1

# Flowers grow in patches in the open: five to twelve plants of mostly one
# kind within a couple of metres. One flower alone in a meadow is invisible.
patches = 0
for _ in range(20000):
    if not flowers or patches >= 260:
        break
    x, y = rng.uniform(-SIZE / 2, SIZE / 2), rng.uniform(-SIZE / 2, SIZE / 2)
    z = sample(height, x, y)
    if not (WATER_LEVEL + 1.2 < z < 26) or sample(slope, x, y) > 0.4 or math.hypot(x, y) < PLATEAU_RADIUS + 2:
        continue
    if sample(forest, x, y) > 0.5:
        continue  # meadow and the wood's edge, not under the trees
    kind = rng.randrange(len(flowers))
    for number in range(rng.randint(5, 12)):
        px, py = x + rng.gauss(0.0, 0.7), y + rng.gauss(0.0, 0.7)
        pz = sample(height, px, py)
        if pz < WATER_LEVEL + 0.6:
            continue
        plant, size = flowers[kind if rng.random() < 0.85 else rng.randrange(len(flowers))]
        add(
            f"Flower.{patches:03d}.{number:02d}",
            plant.mesh,
            undergrowth,
            location=(px, py, pz - 0.02),
            rotation_z=rng.uniform(0, math.tau),
            scale=size * rng.uniform(0.75, 1.3),
            collision="none",
            shadows=False,
            lod_skip=True,
        )
    patches += 1

# --- Rocks -------------------------------------------------------------------

stone = material("Rock", (0.17, 0.165, 0.155), roughness=0.92)
rock_meshes = []
for variant in range(4):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    squash = Vector((rng.uniform(0.8, 1.3), rng.uniform(0.8, 1.3), rng.uniform(0.5, 0.8)))
    for vertex in bm.verts:
        bump = 0.75 + 0.5 * float(value_noise(np.array(vertex.co.x * 1.3 + variant * 9.1), np.array(vertex.co.y * 1.3 + vertex.co.z * 2.7)))
        vertex.co = Vector((vertex.co.x * squash.x, vertex.co.y * squash.y, vertex.co.z * squash.z)) * bump
    rock_meshes.append(mesh_from(bm, f"Rock{variant}", [stone]))

rocks = collection("Rocks")
placed = 0
for _ in range(8000):
    if placed >= 320:
        break
    x, y = rng.uniform(-SIZE / 2, SIZE / 2), rng.uniform(-SIZE / 2, SIZE / 2)
    z = sample(height, x, y)
    if z < WATER_LEVEL - 1 or math.hypot(x, y) < PLATEAU_RADIUS + 4:
        continue
    steep = sample(slope, x, y)
    if rng.random() > 0.15 + steep:
        continue
    scale = rng.uniform(0.35, 1.0) ** 2 * 3.2 + 0.3
    add(
        f"Rock.{placed:03d}",
        rng.choice(rock_meshes),
        rocks,
        location=(x, y, z - 0.25 * scale),
        rotation_z=rng.uniform(0, math.tau),
        scale=scale,
        collision="convex" if scale > 0.9 else "none",
    )
    placed += 1

# --- A few unique structures -------------------------------------------------

ruins = collection("Ruins")
masonry = surface("Masonry", "medieval_blocks_03") or material(
    "Masonry", (0.36, 0.33, 0.28), roughness=0.88, texture=masonry_color, normal_map=masonry_normal
)
MASONRY_TILE = 2.0  # metres covered by one repeat of the texture

pillar = bmesh.new()
shaft = bmesh.ops.create_cone(pillar, cap_ends=True, segments=12, radius1=0.45, radius2=0.4, depth=4.0)
bmesh.ops.translate(pillar, vec=(0, 0, 2.0), verts=shaft["verts"])
box(pillar, (1.2, 1.2, 0.3), (0, 0, 0.15))
box(pillar, (1.1, 1.1, 0.25), (0, 0, 4.1))
box_uv(pillar, MASONRY_TILE)
pillar_mesh = mesh_from(pillar, "Pillar", [masonry])
for i in range(9):
    angle = i / 9 * math.tau + 0.2
    if i in (2, 6):
        continue  # gaps: it's a ruin
    add(
        f"Pillar.{i}",
        pillar_mesh,
        ruins,
        location=(math.cos(angle) * 23, math.sin(angle) * 23, 0),
        rotation_z=angle,
        collision="cylinder",
        collision_radius=0.5,
    )

arch = bmesh.new()
box(arch, (1.4, 1.4, 5.0), (-3.0, 0, 2.5))
box(arch, (1.4, 1.4, 5.0), (3.0, 0, 2.5))
box(arch, (8.6, 1.8, 1.2), (0, 0, 5.6))
box_uv(arch, MASONRY_TILE)
add("Arch", mesh_from(arch, "Arch", [masonry]), ruins, location=(0, -26.5, 0))


def shoreline(angle):
    """Distance from the origin to where the land meets the sea along a bearing."""
    for radius in np.arange(PLATEAU_RADIUS, SIZE / 2, 1.0):
        if sample(height, math.cos(angle) * radius, math.sin(angle) * radius) < WATER_LEVEL:
            return float(radius)
    return SIZE / 2


# A long pier: one closed mesh that crosses chunk borders, so the exporter has
# to slice a solid as well as the terrain sheet.
bearing = math.radians(-35)
shore = shoreline(bearing)
pier_length = 120.0
pier = bmesh.new()
box(pier, (pier_length, 4.0, 4.0), (0, 0, 0))
box_uv(pier, MASONRY_TILE)
middle = shore + pier_length / 2 - 18
add(
    "Pier",
    mesh_from(pier, "Pier", [masonry]),
    ruins,
    location=(math.cos(bearing) * middle, math.sin(bearing) * middle, WATER_LEVEL + 0.9 - 2.0),
    rotation_z=bearing,
)

# --- Markers -----------------------------------------------------------------

markers = collection("Markers")


def marker(name, x, y, facing=0.0, display="SINGLE_ARROW", size=2.0, scale=(1, 1, 1), lift=0.2, **props):
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = display
    empty.empty_display_size = size
    empty.location = (x, y, sample(height, x, y) + lift)
    empty.rotation_euler = (0, 0, facing)
    empty.scale = scale
    markers.objects.link(empty)
    for key, value in props.items():
        empty[key] = value


marker("spawn", 0, 4)

# An effect placed in the map: the game starts a campfire here when the player
# is near. Any Empty with an "fx" property works the same way.
CAMPFIRE = (-3.0, 1.0)
# It can also be used: "interact" names it for the game's code, which decides
# what using it does (here, putting the fire out and lighting it again).
marker("campfire", *CAMPFIRE, fx="campfire", interact="campfire", prompt="Put out the fire", radius=2.2)

# A trigger zone: the game hears about it when the player walks under the
# arch. The zone is the Empty's cube as drawn, 7 m wide, 3 m deep, 5 m high.
marker("ruins_gate", 0, -26.5, display="CUBE", size=1.0, scale=(3.5, 1.5, 2.5), lift=2.5, trigger="ruins_gate")
fire_stone_meshes = []  # the rock shapes again, cut from the same stone as the ruins
for rock_mesh in rock_meshes:
    bm = bmesh.new()
    bm.from_mesh(rock_mesh)
    box_uv(bm, MASONRY_TILE * 3)
    fire_stone_meshes.append(mesh_from(bm, rock_mesh.name.replace("Rock", "FireStone"), [masonry]))
for i in range(7):
    angle = i / 7 * math.tau
    add(
        f"FireStone.{i}",
        fire_stone_meshes[i % len(fire_stone_meshes)],
        ruins,
        location=(CAMPFIRE[0] + math.cos(angle) * 0.5, CAMPFIRE[1] + math.sin(angle) * 0.5, 0.02),
        rotation_z=rng.uniform(0, math.tau),
        scale=rng.uniform(0.13, 0.19),
        collision="none",
    )
marker("spawn_pier", math.cos(bearing) * (shore - 4), math.sin(bearing) * (shore - 4), facing=bearing + math.pi / 2)
# A door to another level: the way back to the town, from the landing place.
marker("boat_to_town", math.cos(bearing) * (shore - 9), math.sin(bearing) * (shore - 9), door="town#spawn", prompt="Sail to the town", radius=3.5)
peak = np.unravel_index(np.argmax(height), height.shape)
marker("poi_summit", float(X[peak]), float(Y[peak]), kind="viewpoint")

# --- Save --------------------------------------------------------------------

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
if not argv:
    raise SystemExit("usage: blender -b --factory-startup --python make_test_map.py -- <output.blend>")
path = os.path.abspath(argv[0])
os.makedirs(os.path.dirname(path), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=path)
print(
    f"[make_test_map] {path}: terrain {2 * len(quads):,} triangles, "
    f"height {height.min():.1f}..{height.max():.1f} m, {len(trees.objects)} trees, {len(rocks.objects)} rocks"
)
