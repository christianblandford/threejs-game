"""
Export the open .blend as a streamable map for the game.

    blender -b my_map.blend --python tools/blender/export_map.py -- --out public/maps/my_map

(or `npm run map:export -- my_map.blend`, which finds Blender for you.)

The scene is cut into a grid of square chunks. Each chunk becomes two small
.glb files, plus a `map.json` manifest that the game reads to decide what to
load around the player:

- the full-detail version: your objects as they are, with materials, instancing
  and custom properties intact;
- the far version: everything in the chunk decimated and merged into ONE mesh
  with ONE shared material, each surface tinted with its material's base
  colour. A distant chunk is then a single cheap draw call. (Textures are not
  carried over: a textured surface is tinted with its texture's average
  colour, which reads the same at that distance.)

What goes where
---------------
- Mesh objects are put in the chunk that contains their centre.
- Meshes much bigger than a chunk (terrain, roads, long walls) are sliced along
  chunk borders, so only the nearby part is ever loaded. Shading stays seamless
  across the cuts.
- Objects that share mesh data (Alt+D duplicates) stay instanced: one mesh in
  the file, drawn many times.
- Empties become "markers" in the manifest (name, position, facing, size,
  custom properties). Name one `spawn` to set where the player starts. Give
  one a custom property `fx` (an effect name such as "campfire", optionally
  with `fx_scale`) and the game runs that particle effect there.
- An Empty with a custom property `trigger` (a name) is a zone the game is
  told about when something walks in or out. Its volume is the Empty as drawn
  in the viewport: display it as a Cube or a Sphere and scale it to fit. It
  can be turned about the vertical axis, not tilted.
- An Empty with a custom property `interact` (a name) is something the player
  can use by pressing the interact key nearby. `prompt` is the text shown
  ("Open the gate") and `radius` how close the player must be (2 m if unset).

Textures
--------
Image textures are written once per map into `textures/`, each file named
after a hash of its contents, and the chunk files refer to them by relative
path instead of carrying their own copies. A texture used by fifty chunks is
one file, one download and one copy on the GPU. The far versions have none.

They are compressed to KTX2 (Basis Universal) with mipmaps, a format that
stays compressed in GPU memory: colour and data maps (roughness/metallic,
occlusion) in the compact ETC1S form, normal maps in high-fidelity UASTC,
which is why those are stored at half size by default (--normal-scale). The
encoder is Basis Universal's own, as built into `@babylonjs/ktx2encoder` (the one
packaging of it that runs under Node; nothing else of Babylon's is used), so this needs `npm install`
to have been done and `node` on the PATH (or NODE=/path/to/node). Without
them, or with --no-ktx2, the textures are shared all the same but stay in the
format Blender exported them in (PNG, JPEG or WebP). Encoded textures are
kept between exports; only new or changed ones take time. The same step keeps
a copy of three's Basis transcoder in `public/basis/`, which is where the
game loads it from.

Per-object custom properties (Object Properties > Custom Properties)
--------------------------------------------------------------------
    collision      "mesh" | "convex" | "box" | "sphere" | "cylinder" | "none"
                   Physics shape. Default: "mesh" for unique meshes, "box" for
                   instanced ones.
    collision_radius   Radius in metres for "sphere" / "cylinder" (default: from size).
    collider_only  true -> invisible, collision only.
    shadows        false -> does not cast shadows.
    always_loaded  true -> goes in global.glb and never unloads (water, skyline).
    split          true/false -> force or forbid slicing along chunk borders.
    skirt          true/false -> force or forbid the hidden lip hung below the edges
                   of each sliced piece (it hides gaps between a chunk drawn in
                   detail and its neighbour drawn far). Default: only open sheets
                   that lie mostly flat, i.e. ground.
    lod_ratio      Fraction of triangles kept in the far version (default --lod-ratio).
    lod_skip       true -> left out of the far version entirely.
    far_mesh       The name of another mesh to stand in for this object far away,
                   instead of a thinned copy of itself. A tree whose leaves are
                   cut-out cards would thin into confetti; its stand-in is a few
                   crossed flat cards showing a picture of it. (Give that mesh a
                   fake user so Blender saves it.) If the stand-in's material has
                   the custom property `impostor`, its texture is carried into the
                   far version and its UVs are kept; any other stand-in is reduced
                   to plain colour, as everything far away is.
    ground_cover   A name, e.g. "grass": the game plants ground cover on this object.
                   Paint WHERE in Weight Paint mode, in a vertex group called
                   `ground_cover` (1 thick, 0 bare); with no such group it grows
                   everywhere. (The weights travel to the game as a second UV map.)
    blend_layer    The name of a second material to blend into this object's own,
    blend_mask     and of the image that says where: white shows the second
                   material, black the object's own, and the edge between them
                   breaks up naturally. A path worn into grass. The image is
                   laid over the object from above, its corners at the corners
                   of the object's bounding box (paint it in Texture Paint on a
                   UV map made with Project from View (Bounds), looking down).
                   Give the image and the second material a fake user (the
                   shield button), or Blender will not save them: nothing in
                   the file uses either.
    height_map     true -> this object is the lie of the land: its height is baked
                   into an image the game can read anywhere (water uses it to
                   tell shallows from depths). Put it on the terrain.
    mesh_id        Set automatically on linked duplicates; lets the game batch them.
                   (Their mesh is written once, into global.glb, and each
                   placing in a chunk file is a placeholder that names it.)
    export         false -> ignored.

Anything else you add is passed through and readable in the game as
`mesh.metadata.gltf.extras`.

Run with `-- --help` for the command-line options.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

MANIFEST_VERSION = 1
COVER_GROUP = "ground_cover"  # vertex group saying where ground cover grows

TEXTURE_FOLDER = "textures"
KTX2_COLOUR_QUALITY = 128  # ETC1S quality level, 1-255 (128 is the encoder's own default)
KTX2_MAX_PIXELS = 12_582_912  # the most the encoder takes in one image
KTX2_RECIPE = 2  # raise when the way textures are encoded changes, so kept files are redone


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="export_map.py", description="Export a .blend as a chunked map.")
    parser.add_argument("--out", required=True, help="Output folder, e.g. public/maps/my_map")
    parser.add_argument("--name", help="Map name in the manifest (default: the .blend file name)")
    parser.add_argument("--chunk", type=float, default=64.0, help="Chunk edge length in metres (default 64)")
    parser.add_argument("--no-lod", action="store_true", help="Skip the decimated far version of each chunk")
    parser.add_argument("--lod-ratio", type=float, default=0.2, help="Triangles kept in the far version (default 0.2)")
    parser.add_argument("--lod-min-size", type=float, default=1.5, help="Objects smaller than this (m) are dropped from the far version")
    parser.add_argument("--skirt", type=float, default=3.0, help="Depth (m) of the hidden lip around sliced terrain that covers seams between detail levels; 0 disables")
    parser.add_argument("--draco", action="store_true", help="Draco-compress geometry (smaller files; the game then needs the Draco decoder)")
    parser.add_argument("--no-ktx2", action="store_true", help="Leave textures as PNG/JPEG/WebP instead of compressing them to KTX2")
    parser.add_argument("--max-texture-size", type=int, default=2048, help="Textures with a longer edge than this (pixels) are scaled down (default 2048)")
    parser.add_argument("--normal-scale", type=float, default=0.5, help="Normal maps are stored at this fraction of their size (default 0.5: a quarter of the download; 1 keeps them whole)")
    parser.add_argument("--texture-quality", type=int, default=KTX2_COLOUR_QUALITY, help=f"KTX2 quality of colour textures, 1-255: higher is cleaner and larger (default {KTX2_COLOUR_QUALITY})")
    return parser.parse_args(argv)


# --- Coordinates -------------------------------------------------------------
# Blender is Z-up; the game is Y-up and left-handed. A Blender point (x, y, z)
# ends up at (-x, z, -y) in the game. The manifest is written in game space so
# the loader never has to think about it.


def to_game(v):
    return [round(-v.x, 4), round(v.z, 4), round(-v.y, 4)]


def bounds_to_game(lo, hi):
    return [round(-hi.x, 4), round(lo.z, 4), round(-hi.y, 4)], [round(-lo.x, 4), round(hi.z, 4), round(-lo.y, 4)]


def world_bounds(obj):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo, hi


def marker_volume(obj):
    """An Empty's size as drawn in the viewport, in game axes: what a trigger zone covers."""
    scale = obj.matrix_world.to_scale()
    half = obj.empty_display_size
    if obj.empty_display_type == "SPHERE":
        diameter = round(2 * half * max(abs(scale.x), abs(scale.y), abs(scale.z)), 4)
        return {"shape": "sphere", "size": [diameter, diameter, diameter]}
    # Blender's local X, Y, Z are the game's width, depth and height.
    return {"shape": "box", "size": [round(2 * half * abs(scale.x), 4), round(2 * half * abs(scale.z), 4), round(2 * half * abs(scale.y), 4)]}


def custom_props(obj):
    """User-defined custom properties with JSON-friendly values."""
    props = {}
    for key in obj.keys():
        value = obj[key]
        if isinstance(value, (bool, int, float, str)):
            props[key] = value
    return props


def truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def triangle_count(mesh):
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def upstream_image(node):
    """The nearest image texture feeding a shader node, looking back through whatever is plugged into it."""
    queue, seen = [node], set()
    while queue:
        node = queue.pop(0)
        if node.type == "TEX_IMAGE" and node.image is not None and node.image.size[0] > 0:
            return node.image
        if node.name not in seen:
            seen.add(node.name)
            queue.extend(link.from_node for socket in node.inputs for link in socket.links)
    return None


AVERAGE_COLOURS = {}  # image name -> rgb


def average_colour(image):
    """An image's mean colour in linear RGB, with transparent pixels counting for less."""
    if image.name not in AVERAGE_COLOURS:
        pixels = np.empty(image.size[0] * image.size[1] * image.channels, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(-1, image.channels).astype(np.float64)
        rgb = pixels[:, :3]
        if not image.is_float and image.colorspace_settings.name == "sRGB":  # 8-bit colour is stored display-encoded
            rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        opacity = pixels[:, 3:] if image.channels == 4 and pixels[:, 3].any() else np.ones((len(pixels), 1))
        AVERAGE_COLOURS[image.name] = tuple(float(c) for c in (rgb * opacity).sum(axis=0) / opacity.sum())
    return AVERAGE_COLOURS[image.name]


def colour_source(socket):
    """What feeds one colour input: (colour attribute name or None, flat rgb), or None if it can't be told."""
    if not socket.is_linked:
        return None, tuple(socket.default_value[:3])
    source = socket.links[0].from_node
    if source.type == "VERTEX_COLOR":
        return source.layer_name or "", (1.0, 1.0, 1.0)
    if source.type in ("MIX", "MIX_RGB") and source.blend_type == "MULTIPLY":
        # A texture tinted by a colour, or by painted vertex colours: the product of the two.
        inputs = [source.inputs["A"], source.inputs["B"]] if source.type == "MIX" else [source.inputs["Color1"], source.inputs["Color2"]]
        parts = [colour_source(colour_input) for colour_input in inputs]
        if None not in parts:
            attribute = next((name for name, _rgb in parts if name is not None), None)
            return attribute, tuple(a * b for a, b in zip(parts[0][1], parts[1][1]))
    image = upstream_image(source)
    return (None, average_colour(image)) if image is not None else None


def base_colour(material):
    """
    Where a material's base colour comes from: (colour attribute name or None,
    flat rgb). With an attribute, the colour is the attribute times the rgb.
    """
    if material is None:
        return None, (0.8, 0.8, 0.8)
    if material.node_tree:
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            found = colour_source(node.inputs["Base Color"])
            if found is not None:
                return found
            break  # procedural: fall back to the viewport colour
    return None, tuple(material.diffuse_color[:3])


# --- Textures ----------------------------------------------------------------
# Blender's exporter puts a copy of every image a .glb uses inside it. We let
# it, then take the images back out: each distinct image is written once to
# `textures/` and the .glb is left holding a relative URI.

# glTF material slots that hold colour. Normal maps are recognised by name;
# every other slot (roughness/metallic, occlusion, ...) holds plain data.
COLOUR_SLOTS = {"baseColorTexture", "emissiveTexture", "specularColorTexture", "sheenColorTexture", "diffuseTransmissionColorTexture"}
ROLES = ("color", "data", "normal")  # an image used in several slots is treated as the last of these that applies
IMAGE_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
BLENDER_FORMAT = {".png": "PNG", ".jpg": "JPEG", ".webp": "WEBP"}


def read_glb(path):
    """A .glb file as (glTF document, binary chunk)."""
    with open(path, "rb") as handle:
        data = handle.read()
    document_length = struct.unpack_from("<I", data, 12)[0]
    binary_start = 20 + document_length + 8
    binary_length = struct.unpack_from("<I", data, binary_start - 8)[0]
    return json.loads(data[20 : 20 + document_length]), data[binary_start : binary_start + binary_length]


def write_glb(path, document, binary):
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(text) + 8 + len(binary)))
        handle.write(struct.pack("<I4s", len(text), b"JSON") + text)
        handle.write(struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def renumber_buffer_views(node, new_index):
    """Fixes every `bufferView` reference in a glTF document after some views were removed."""
    if isinstance(node, list):
        for item in node:
            renumber_buffer_views(item, new_index)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "bufferView" and isinstance(value, int):
                node[key] = new_index[value]
            elif key != "extras":
                renumber_buffer_views(value, new_index)


def texture_image(texture):
    """The index of the image a glTF texture shows, whichever extension holds the reference."""
    if "source" in texture:
        return texture["source"]
    return next(extension["source"] for extension in texture.get("extensions", {}).values() if "source" in extension)


def image_roles(document):
    """What each image in a glTF document is used as: image index -> "color", "normal" or "data"."""
    roles = {}

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("Texture") and isinstance(value, dict) and "index" in value:
                    role = "normal" if key.lower().endswith("normaltexture") else "color" if key in COLOUR_SLOTS else "data"
                    image = texture_image(document["textures"][value["index"]])
                    roles[image] = max(roles.get(image, "color"), role, key=ROLES.index)
                else:
                    visit(value)

    visit(document.get("materials", []))
    return roles


class Textures:
    """The map's shared texture folder, and the .glb files that refer to it."""

    def __init__(self, opts, out):
        self.opts = opts
        self.folder = os.path.join(out, TEXTURE_FOLDER)
        self.sources = {}  # file name (content hash + suffix) -> the image file as Blender exported it
        self.documents = {}  # .glb path -> its glTF document, for the files that use textures
        self.files = []  # what ends up in the folder
        self.ktx2 = False

    def take_from(self, path):
        """Moves the images embedded in a .glb out of it, leaving relative URIs behind."""
        document, binary = read_glb(path)
        views = document.get("bufferViews", [])
        embedded = {image["bufferView"] for image in document.get("images", [])}
        if not embedded:
            return
        for image in document["images"]:
            view = views[image.pop("bufferView")]
            start = view.get("byteOffset", 0)
            data = binary[start : start + view["byteLength"]]
            name = hashlib.sha256(data).hexdigest()[:16] + IMAGE_SUFFIX[image["mimeType"]]
            self.sources[name] = data
            image["uri"] = f"{TEXTURE_FOLDER}/{name}"

        # Repack the binary chunk without the image data.
        kept, new_index = bytearray(), {}
        for index, view in enumerate(views):
            if index in embedded:
                continue
            start = view.get("byteOffset", 0)
            kept += b"\0" * (-len(kept) % 4)
            view["byteOffset"] = len(kept)
            kept += binary[start : start + view["byteLength"]]
            new_index[index] = len(new_index)
        document["bufferViews"] = [view for index, view in enumerate(views) if index not in embedded]
        renumber_buffer_views(document, new_index)
        document["buffers"][0]["byteLength"] = len(kept)
        write_glb(path, document, bytes(kept))
        self.documents[path] = document

    def finish(self):
        """Writes the texture files -- compressed if possible -- and points the .glb files at them."""

        def uses(document):
            """(image, source file, role) for each image in a document."""
            roles = image_roles(document)
            return [(image, os.path.basename(image["uri"]), roles.get(index, "color")) for index, image in enumerate(document["images"])]

        wanted = sorted({(source, role) for document in self.documents.values() for _image, source, role in uses(document)})
        written = {}  # (source file, role) -> the file the game should load for it
        if wanted:
            os.makedirs(self.folder, exist_ok=True)
            with tempfile.TemporaryDirectory() as scratch:
                written = None if self.opts.no_ktx2 else self.compress(wanted, scratch)
                self.ktx2 = written is not None
                if written is None:
                    written = self.copy(wanted, scratch)
        self.files = sorted(set(written.values()))

        for path, document in self.documents.items():
            changed = False
            for image, source, role in uses(document):
                uri = f"{TEXTURE_FOLDER}/{written[(source, role)]}"
                changed |= image["uri"] != uri
                image["uri"] = uri
                if self.ktx2:
                    image["mimeType"] = "image/ktx2"
            if self.ktx2:
                # KHR_texture_basisu: a texture names its image through the extension.
                for texture in document["textures"]:
                    image = texture_image(texture)
                    texture.pop("source", None)
                    texture["extensions"] = {"KHR_texture_basisu": {"source": image}}
                for key in ("extensionsUsed", "extensionsRequired"):
                    document[key] = [name for name in document.get(key, []) if name != "EXT_texture_webp"] + ["KHR_texture_basisu"]
            if changed:
                write_glb(path, document, read_glb(path)[1])

        # Clear out textures from earlier exports that nothing uses any more.
        if os.path.isdir(self.folder):
            for stale in set(os.listdir(self.folder)) - set(self.files):
                os.remove(os.path.join(self.folder, stale))
            if not self.files:
                os.rmdir(self.folder)

    def load(self, source, scratch, multiple=1, max_pixels=None, shrink=1.0):
        """A texture as a temporary Blender image, scaled down to fit the size limits: (image, was it scaled)."""
        path = os.path.join(scratch, source)
        with open(path, "wb") as handle:
            handle.write(self.sources[source])
        image = bpy.data.images.load(path)
        image.colorspace_settings.name = "Non-Color"  # we want the stored values, not scene-linear ones
        image.alpha_mode = "CHANNEL_PACKED"
        width, height = image.size
        scale = min(1.0, self.opts.max_texture_size / max(width, height)) * shrink
        if max_pixels:
            scale = min(scale, math.sqrt(max_pixels / (width * height)))
        fitted = [max(multiple, int(side * scale) // multiple * multiple) for side in (width, height)]
        if fitted != [width, height]:
            image.scale(*fitted)
        return image, fitted != [width, height]

    def copy(self, wanted, scratch):
        """Uncompressed textures: the files Blender exported, saved again only if they are over the size limit."""
        written = {}
        for source in sorted({source for source, _role in wanted}):
            image, scaled = self.load(source, scratch)
            name, suffix = source, os.path.splitext(source)[1]
            if scaled:
                name = hashlib.sha256(f"{source}:{self.opts.max_texture_size}".encode()).hexdigest()[:16] + suffix
                image.file_format = BLENDER_FORMAT[suffix]
                image.filepath_raw = os.path.join(self.folder, name)
                image.save(quality=90)
            else:
                with open(os.path.join(self.folder, name), "wb") as handle:
                    handle.write(self.sources[source])
            bpy.data.images.remove(image)
            for role in ROLES:
                written[(source, role)] = name
        return written

    def compress(self, wanted, scratch):
        """KTX2 textures, encoded by `tools/map.mjs encode-textures` under Node. None if that can't be done."""
        node = os.environ.get("NODE") or shutil.which("node")
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "map.mjs")
        if not node or not os.path.isfile(tool):
            print("[export_map] textures left uncompressed: Node.js not found (put `node` on the PATH or set NODE)")
            return None

        written, jobs = {}, []
        for source, role in wanted:
            quality = self.opts.texture_quality if role == "color" else None
            # Normal maps can't use the compact format without visible damage, so
            # they are by far the largest files; storing them smaller is the saving.
            shrink = self.opts.normal_scale if role == "normal" else 1.0
            recipe = f"{source}:{role}:{quality}:{self.opts.max_texture_size}:{shrink}:{KTX2_RECIPE}"
            name = hashlib.sha256(recipe.encode()).hexdigest()[:16] + ".ktx2"
            written[(source, role)] = name
            if os.path.isfile(os.path.join(self.folder, name)):
                continue  # encoded by an earlier export

            # Hand the encoder plain 8-bit RGBA, top row first (Blender keeps the bottom row first).
            image, _scaled = self.load(source, scratch, multiple=4, max_pixels=KTX2_MAX_PIXELS, shrink=shrink)
            width, height = image.size
            pixels = np.empty(width * height * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            bpy.data.images.remove(image)
            raw = os.path.join(scratch, name + ".rgba")
            np.rint(pixels.reshape(height, width, 4)[::-1] * 255).clip(0, 255).astype(np.uint8).tofile(raw)
            jobs.append({"rgba": raw, "width": width, "height": height, "usage": role, "quality": quality, "out": os.path.join(self.folder, name)})

        # Run even with nothing new to encode: it also keeps the game's copy of the decoder current.
        job_file = os.path.join(scratch, "jobs.json")
        with open(job_file, "w", encoding="utf-8") as handle:
            json.dump(jobs, handle)
        sys.stdout.flush()
        if subprocess.run([node, tool, "encode-textures", job_file]).returncode != 0:
            print("[export_map] textures left uncompressed")
            return None
        return written


class Exporter:
    def __init__(self, opts):
        self.opts = opts
        self.lod = not opts.no_lod
        self.scene = bpy.context.scene
        self.work = bpy.data.collections.new("__map_export__")
        self.scene.collection.children.link(self.work)
        self.temp_meshes = []
        self.far_material = None
        # A material flagged `impostor` (tools/blender/plants.py makes one) holds
        # pictures of trees for flat cards that stand in for them far away.
        # Those cards are merged into each chunk's far mesh like any other
        # stand-in, so the far material carries that picture atlas: cards map
        # to their own picture, everything else to a white patch of it.
        self.cards = next((m for m in bpy.data.materials if truthy(m.get("impostor", False)) and m.users > 0), None)
        self.white_uv = [float(v) for v in self.cards.get("white_uv", (0.5, 0.5))] if self.cards is not None else None
        self.library = {}  # mesh name -> the one full copy of a shared mesh, kept in global.glb
        self.stubs = {}  # mesh name -> its placeholder in the chunk files
        self.added_uv_layers = []  # (mesh, layer name) to take off again afterwards
        self.blends = []  # second materials blended into objects through mask images
        self.height_source = None  # the object whose surface is baked into the height map
        self.swatches = set()
        self.lod_mesh_cache = {}
        self.chunks = {}
        self.global_objects = []
        self.markers = []
        self.textured_files = 0  # how many .glb files refer to the shared textures

    # --- Bookkeeping ---------------------------------------------------------

    def chunk(self, key):
        if key not in self.chunks:
            self.chunks[key] = {"lods": [[], []], "lo": None, "hi": None}
        return self.chunks[key]

    def grow(self, chunk, lo, hi):
        if chunk["lo"] is None:
            chunk["lo"], chunk["hi"] = lo.copy(), hi.copy()
        else:
            chunk["lo"] = Vector((min(chunk["lo"].x, lo.x), min(chunk["lo"].y, lo.y), min(chunk["lo"].z, lo.z)))
            chunk["hi"] = Vector((max(chunk["hi"].x, hi.x), max(chunk["hi"].y, hi.y), max(chunk["hi"].z, hi.z)))

    def new_mesh(self, name):
        mesh = bpy.data.meshes.new(name)
        self.temp_meshes.append(mesh)
        return mesh

    def new_object(self, name, mesh, source):
        obj = bpy.data.objects.new(name, mesh)
        self.work.objects.link(obj)
        for key, value in custom_props(source).items():
            obj[key] = value
        return obj

    def depsgraph(self):
        return bpy.context.evaluated_depsgraph_get()

    def decimated(self, mesh, ratio):
        """A copy of `mesh` with roughly `ratio` of its triangles."""
        holder = bpy.data.objects.new("__decimate__", mesh)
        self.work.objects.link(holder)
        modifier = holder.modifiers.new("decimate", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = ratio
        result = bpy.data.meshes.new_from_object(holder.evaluated_get(self.depsgraph()))
        self.temp_meshes.append(result)
        bpy.data.objects.remove(holder)
        return result

    # --- Scene walk ----------------------------------------------------------

    def collect(self):
        size = self.opts.chunk
        for obj in list(self.scene.objects):
            if obj.name in self.work.objects or not obj.visible_get():
                continue
            if "export" in obj and not truthy(obj["export"]):
                continue

            if obj.type == "EMPTY":
                forward = obj.matrix_world.to_3x3() @ Vector((0.0, -1.0, 0.0))  # Blender "front" is -Y
                self.markers.append(
                    {
                        "name": obj.name,
                        "position": to_game(obj.matrix_world.translation),
                        "yaw": round(math.atan2(-forward.x, -forward.y), 4),
                        **marker_volume(obj),
                        "props": custom_props(obj),
                    }
                )
                continue
            if obj.type != "MESH" or len(obj.data.polygons) == 0:
                continue
            self.cover_weights_to_uv(obj)
            self.note_blend(obj)
            if truthy(obj.get("height_map", False)):
                self.height_source = obj

            in_collider_collection = any(c.name.lower().startswith(("collider", "collision")) for c in obj.users_collection)
            lo, hi = world_bounds(obj)

            if truthy(obj.get("always_loaded", False)):
                self.global_objects.append(self.passthrough(obj, in_collider_collection))
                continue

            extent = max(hi.x - lo.x, hi.y - lo.y)
            split = truthy(obj["split"]) if "split" in obj else extent > size * 1.5
            if split:
                self.slice_object(obj, lo, hi, in_collider_collection)
                continue

            centre = (lo + hi) / 2
            chunk = self.chunk((math.floor(centre.x / size), math.floor(centre.y / size)))
            chunk["lods"][0].append(self.passthrough(obj, in_collider_collection))
            self.grow(chunk, lo, hi)

            too_small = max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z) < self.opts.lod_min_size
            if self.lod and not in_collider_collection and not too_small and not truthy(obj.get("lod_skip", False)):
                chunk["lods"][1].append(self.far_version(obj))

    def cover_weights_to_uv(self, obj):
        """
        Where ground cover grows is painted as a vertex group. glTF has no slot
        for that, so it is carried as a second UV map (u = weight), which also
        comes through slicing intact.
        """
        group = obj.vertex_groups.get(COVER_GROUP)
        if group is None or COVER_GROUP in obj.data.uv_layers:
            return
        mesh = obj.data
        if not mesh.uv_layers:
            mesh.uv_layers.new(name="UVMap")  # the cover map must be the second one
            self.added_uv_layers.append((mesh, "UVMap"))
        weights = np.zeros(len(mesh.vertices), dtype=np.float32)
        for vertex in mesh.vertices:
            for membership in vertex.groups:
                if membership.group == group.index:
                    weights[vertex.index] = membership.weight
        corner_vertex = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", corner_vertex)
        layer = mesh.uv_layers.new(name=COVER_GROUP, do_init=False)
        layer.data.foreach_set("uv", np.stack([weights[corner_vertex], np.zeros(len(corner_vertex), dtype=np.float32)], axis=1).ravel())
        self.added_uv_layers.append((mesh, COVER_GROUP))

    def note_blend(self, obj):
        """Records a `blend_layer` / `blend_mask` pair for the manifest, and makes sure the layer's material is exported."""
        layer = bpy.data.materials.get(str(obj.get("blend_layer", "")))
        mask = bpy.data.images.get(str(obj.get("blend_mask", "")))
        if layer is None or mask is None or not obj.data.materials or obj.data.materials[0] is None:
            return
        lo, hi = world_bounds(obj)
        width, depth = hi.x - lo.x, hi.y - lo.y
        # Mask u, v from a position in the game's axes (x = -Blender x, z = -Blender y).
        mapping = [-1 / width, -1 / depth, -lo.x / width, -lo.y / depth]
        self.blends.append({"material": obj.data.materials[0].name, "layer": layer.name, "mask": mask, "map": [round(v, 8) for v in mapping]})
        if layer.name in self.swatches:
            return
        # Nothing may be drawn with the layer's material itself, and a material
        # no mesh uses is not exported: a speck of one, buried, carries it.
        self.swatches.add(layer.name)
        mesh = self.new_mesh(f"blend:{layer.name}")
        mesh.from_pydata([(0, 0, -200), (0.01, 0, -200), (0, 0.01, -200)], [], [(0, 1, 2)])
        mesh.uv_layers.new(name="UVMap")
        mesh.materials.append(layer)
        swatch = bpy.data.objects.new(f"blend:{layer.name}", mesh)
        self.work.objects.link(swatch)
        swatch["collision"] = "none"
        swatch["shadows"] = False
        swatch["lod_skip"] = True
        self.global_objects.append(swatch)

    def bake_heights(self, out, size=1024):
        """
        The terrain's height as an image, for the game to look up without the
        mesh: 16 bits per pixel, the high byte in red and the low byte in
        green (an 8-bit image has 256 levels, far too few for a shoreline).
        Read it unfiltered and combine the bytes; see engine/water.ts.
        """
        obj = self.height_source
        evaluated = obj.evaluated_get(self.depsgraph())
        source = bmesh.new()
        source.from_mesh(evaluated.to_mesh())
        evaluated.to_mesh_clear()
        source.transform(obj.matrix_world)
        tree = BVHTree.FromBMesh(source)
        lo, hi = world_bounds(obj)
        low, high = lo.z - 0.5, hi.z + 0.5
        down = Vector((0.0, 0.0, -1.0))
        levels = np.zeros((size, size), dtype=np.float64)
        for row in range(size):
            y = lo.y + (row + 0.5) / size * (hi.y - lo.y)
            for column in range(size):
                x = lo.x + (column + 0.5) / size * (hi.x - lo.x)
                hit = tree.ray_cast(Vector((x, y, high)), down)[0]
                levels[row, column] = hit.z if hit is not None else low
        source.free()
        packed = np.clip((levels - low) / (high - low), 0, 1) * 65535
        pixels = np.zeros((size, size, 4), dtype=np.float32)
        pixels[..., 0] = np.floor(packed / 256) / 255
        pixels[..., 1] = np.floor(packed % 256) / 255
        pixels[..., 3] = 1
        image = bpy.data.images.new("map_heights", size, size, alpha=False)
        image.colorspace_settings.name = "Non-Color"
        image.pixels.foreach_set(pixels.ravel())
        os.makedirs(os.path.join(out, "masks"), exist_ok=True)
        image.filepath_raw = os.path.join(out, "masks", "heights.png")
        image.file_format = "PNG"
        image.save()
        bpy.data.images.remove(image)
        width, depth = hi.x - lo.x, hi.y - lo.y
        return {
            "file": "masks/heights.png",
            "size": size,
            # u, v from a position in the game's axes, as for blend masks; then height = low + value * range.
            "map": [round(v, 8) for v in (-1 / width, -1 / depth, -lo.x / width, -lo.y / depth)],
            "low": round(low, 4),
            "range": round(high - low, 4),
        }

    def stub(self, mesh):
        """
        A placeholder for a shared mesh: one speck of a triangle per material
        it uses, so it exports as the same number of parts. The game swaps
        each part for an instance of the real one from the library.
        """
        if mesh.name not in self.stubs:
            used = np.empty(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", used)
            slots = sorted(set(used.tolist()))
            speck = self.new_mesh(f"{mesh.name}__stub")
            for material in mesh.materials:
                speck.materials.append(material)
            vertices = [corner for _ in slots for corner in ((0.0, 0.0, 0.0), (0.001, 0.0, 0.0), (0.0, 0.0, 0.001))]
            speck.from_pydata(vertices, [], [(i * 3, i * 3 + 1, i * 3 + 2) for i in range(len(slots))])
            speck.polygons.foreach_set("material_index", np.asarray(slots, dtype=np.int32))
            speck.update()
            self.stubs[mesh.name] = speck
        return self.stubs[mesh.name]

    def passthrough(self, obj, collider_only):
        """The object itself, or a stand-in if it needs an extra flag or shares its mesh."""
        # Objects that share mesh data (linked duplicates: the same tree placed
        # 900 times) are drawn by the game as one batch, from one copy of the
        # mesh. That copy goes into the always-loaded file, once, flagged
        # `library`; the chunk files carry only a placeholder at each placing,
        # tagged with the mesh's name. Without this every chunk file would
        # hold its own copy of every tree that stands in it.
        if obj.data.users > 1 and not obj.modifiers and not collider_only:
            mesh_id = str(obj.get("mesh_id", obj.data.name))
            if mesh_id not in self.library:
                entry = bpy.data.objects.new(f"library:{mesh_id}", obj.data)
                self.work.objects.link(entry)
                entry["mesh_id"] = mesh_id
                entry["library"] = True
                self.library[mesh_id] = entry
                self.global_objects.append(entry)
            stand_in = self.new_object(obj.name, self.stub(obj.data), obj)
            stand_in.matrix_world = obj.matrix_world.copy()
            stand_in["mesh_id"] = mesh_id
            return stand_in
        if not collider_only:
            return obj
        stand_in = self.new_object(obj.name, obj.data, obj)
        stand_in.matrix_world = obj.matrix_world.copy()
        stand_in["collider_only"] = True
        return stand_in

    def far_version(self, obj):
        """A low-detail stand-in. Instanced objects keep sharing one (decimated) mesh."""
        cache_key = None if obj.modifiers else obj.data.name
        mesh = self.lod_mesh_cache.get(cache_key) if cache_key else None
        if mesh is None:
            base = obj.data
            if obj.modifiers:
                base = bpy.data.meshes.new_from_object(obj.evaluated_get(self.depsgraph()))
                self.temp_meshes.append(base)
            stand_in_mesh = bpy.data.meshes.get(str(obj.get("far_mesh", "")))
            if stand_in_mesh is not None:
                base = stand_in_mesh
                cache_key = None  # already as simple as wanted: used as it is
            triangles = triangle_count(base)
            ratio = 1.0 if stand_in_mesh is not None else float(obj.get("lod_ratio", self.opts.lod_ratio))
            # Small props add up when there are hundreds per chunk, so they're
            # reduced too -- but never below a recognisable silhouette.
            mesh = base if triangles <= 32 or ratio >= 1.0 else self.decimated(base, max(ratio, 32 / triangles))
            if cache_key:
                self.lod_mesh_cache[cache_key] = mesh
        stand_in = self.new_object(obj.name, mesh, obj)
        stand_in.matrix_world = obj.matrix_world.copy()
        return stand_in

    def merge_far(self, objects, name):
        """Bakes a chunk's far stand-ins into one mesh with one vertex-coloured material."""
        all_positions, all_normals, all_colours, all_uvs = [], [], [], []
        for obj in objects:
            mesh = obj.data
            mesh.calc_loop_triangles()
            triangles = len(mesh.loop_triangles)
            if triangles == 0:
                continue
            corners = np.empty(triangles * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("loops", corners)
            faces = np.empty(triangles, dtype=np.int32)
            mesh.loop_triangles.foreach_get("polygon_index", faces)
            corner_vertex = np.empty(len(mesh.loops), dtype=np.int32)
            mesh.loops.foreach_get("vertex_index", corner_vertex)
            coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", coords)
            corner_normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
            mesh.corner_normals.foreach_get("vector", corner_normals)

            matrix = np.array(obj.matrix_world, dtype=np.float64)
            linear = matrix[:3, :3]
            positions = coords.reshape(-1, 3)[corner_vertex[corners]] @ linear.T + matrix[:3, 3]
            normals = corner_normals.reshape(-1, 3)[corners] @ np.linalg.inv(linear)
            lengths = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = normals / np.where(lengths > 1e-12, lengths, 1.0)

            colours = np.ones((triangles * 3, 3), dtype=np.float32)
            uvs = np.tile(np.asarray(self.white_uv or (0.5, 0.5), dtype=np.float32), (triangles * 3, 1))
            slot_of_face = np.zeros(len(mesh.polygons), dtype=np.int32)
            mesh.polygons.foreach_get("material_index", slot_of_face)
            slot_of_corner = np.repeat(slot_of_face[faces], 3)
            for slot, material in enumerate(list(mesh.materials) or [None]):
                mask = slot_of_corner == slot
                if not mask.any():
                    continue
                if material is not None and material == self.cards and mesh.uv_layers.active is not None:
                    # A picture card: its own place in the atlas, untinted.
                    layer = np.empty(len(mesh.loops) * 2, dtype=np.float32)
                    mesh.uv_layers.active.data.foreach_get("uv", layer)
                    uvs[mask] = layer.reshape(-1, 2)[corners][mask]
                    continue
                attribute_name, rgb = base_colour(material)
                attribute = None
                if attribute_name is not None:
                    attribute = mesh.color_attributes.get(attribute_name) or mesh.color_attributes.active_color
                if attribute is not None:
                    data = np.empty(len(attribute.data) * 4, dtype=np.float32)
                    attribute.data.foreach_get("color", data)
                    data = data.reshape(-1, 4)[:, :3]
                    per_corner = data[corner_vertex] if attribute.domain == "POINT" else data
                    colours[mask] = per_corner[corners][mask] * np.asarray(rgb, dtype=np.float32)
                else:
                    colours[mask] = rgb

            if np.linalg.det(linear) < 0:  # mirrored object: keep faces pointing outward
                order = np.arange(triangles * 3).reshape(-1, 3)[:, [0, 2, 1]].ravel()
                positions, normals, colours, uvs = positions[order], normals[order], colours[order], uvs[order]
            all_positions.append(positions)
            all_normals.append(normals)
            all_colours.append(colours)
            all_uvs.append(uvs)

        if not all_positions:
            return None
        positions = np.concatenate(all_positions)
        normals = np.concatenate(all_normals)
        colours = np.concatenate(all_colours)
        uvs = np.concatenate(all_uvs)
        count = len(positions)

        # Weld corners that sit at the same point, so the file stores each
        # vertex once (normals and colours stay per corner).
        welded, index = np.unique(np.round(positions, 4), axis=0, return_inverse=True)
        triangles = index.reshape(-1, 3)
        keep = (triangles[:, 0] != triangles[:, 1]) & (triangles[:, 1] != triangles[:, 2]) & (triangles[:, 0] != triangles[:, 2])
        if not keep.all():  # slivers collapsed by the weld
            triangles = triangles[keep]
            corner_keep = np.repeat(keep, 3)
            normals, colours, uvs = normals[corner_keep], colours[corner_keep], uvs[corner_keep]
            count = len(normals)

        merged = self.new_mesh(name)
        merged.from_pydata(welded.tolist(), [], triangles.tolist())
        merged.update()
        merged.shade_smooth()
        paint = merged.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
        paint.data.foreach_set("color", np.concatenate([colours, np.ones((count, 1), dtype=np.float32)], axis=1).ravel())
        merged.color_attributes.active_color = paint
        merged.normals_split_custom_set(normals.tolist())
        if self.cards is not None:
            merged.uv_layers.new(name="UVMap").data.foreach_set("uv", uvs.ravel())

        if self.far_material is None:
            material = bpy.data.materials.new("MapFar")
            if material.node_tree is None:
                material.use_nodes = True
            nodes, links = material.node_tree.nodes, material.node_tree.links
            shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
            if shader is None:
                shader = nodes.new("ShaderNodeBsdfPrincipled")
                output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
                links.new(shader.outputs["BSDF"], output.inputs["Surface"])
            shader.inputs["Roughness"].default_value = 0.9
            attribute = nodes.new("ShaderNodeVertexColor")
            attribute.layer_name = "Col"
            picture = None
            if self.cards is not None:
                picture = next((n.image for n in self.cards.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None), None)
            if picture is None:
                links.new(attribute.outputs["Color"], shader.inputs["Base Color"])
                material.use_backface_culling = True
            else:
                # Vertex colour x the picture atlas, cut out where the picture is
                # clear (the node pattern Blender's exporter writes as a cut-out).
                image = nodes.new("ShaderNodeTexImage")
                image.image = picture
                mix = nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MULTIPLY"
                mix.inputs["Factor"].default_value = 1.0
                links.new(image.outputs["Color"], mix.inputs["A"])
                links.new(attribute.outputs["Color"], mix.inputs["B"])
                links.new(mix.outputs["Result"], shader.inputs["Base Color"])
                below = nodes.new("ShaderNodeMath")
                below.operation = "LESS_THAN"
                below.inputs[1].default_value = float(self.cards.get("alpha_cutoff", 0.5))
                invert = nodes.new("ShaderNodeMath")
                invert.operation = "SUBTRACT"
                invert.inputs[0].default_value = 1.0
                links.new(image.outputs["Alpha"], below.inputs[0])
                links.new(below.outputs["Value"], invert.inputs[1])
                links.new(invert.outputs["Value"], shader.inputs["Alpha"])
                material.use_backface_culling = False  # cards are seen from both sides
                material["impostor"] = True
            self.far_material = material
        merged.materials.append(self.far_material)

        obj = bpy.data.objects.new(name, merged)
        self.work.objects.link(obj)
        obj["collision"] = "none"
        return obj

    # --- Slicing big meshes along chunk borders --------------------------------

    def slice_object(self, obj, lo, hi, collider_only):
        size = self.opts.chunk
        evaluated = obj.evaluated_get(self.depsgraph())
        source = bmesh.new()
        source.from_mesh(evaluated.to_mesh())
        evaluated.to_mesh_clear()
        source.transform(obj.matrix_world)
        if obj.matrix_world.determinant() < 0:
            bmesh.ops.reverse_faces(source, faces=source.faces)
        bmesh.ops.triangulate(source, faces=source.faces)
        source.normal_update()
        source.verts.ensure_lookup_table()
        source.faces.ensure_lookup_table()

        smooth = any(face.smooth for face in source.faces)
        # The hidden lip is for ground: an open sheet lying mostly flat, whose
        # near and far versions would otherwise show sky through the seam
        # between chunks. An open mesh is not enough to go by: a wall built
        # face by face is open at every edge, and a lip hung from each of its
        # faces is a curtain of smeared texture in front of the wall.
        is_open = any(edge.is_boundary for edge in source.edges)
        area = sum(face.calc_area() for face in source.faces)
        lying = sum(face.calc_area() * face.normal.z for face in source.faces) / area if area > 0 else 0.0
        wants_skirt = truthy(obj["skirt"]) if "skirt" in obj else (is_open and lying > 0.5)
        skirt = self.opts.skirt if (wants_skirt and self.lod) else 0.0
        tree = BVHTree.FromBMesh(source)

        def source_normal(position):
            """The smooth normal of the un-cut mesh at this point, so cuts don't show as lighting seams."""
            location, _normal, index, _distance = tree.find_nearest(position)
            if index is None:
                return Vector((0.0, 0.0, 1.0))
            corners = source.faces[index].verts
            weights = poly_3d_calc([v.co for v in corners], location)
            normal = Vector((0.0, 0.0, 0.0))
            for vertex, weight in zip(corners, weights):
                normal += vertex.normal * weight
            return normal.normalized() if normal.length_squared > 1e-12 else Vector((0.0, 0.0, 1.0))

        def cut(mesh, axis, position, keep_above):
            normal = Vector((1.0, 0.0, 0.0)) if axis == 0 else Vector((0.0, 1.0, 0.0))
            bmesh.ops.bisect_plane(
                mesh,
                geom=mesh.verts[:] + mesh.edges[:] + mesh.faces[:],
                dist=1e-5,
                plane_co=normal * position,
                plane_no=normal,
                clear_inner=keep_above,
                clear_outer=not keep_above,
            )

        def settle(mesh):
            """
            Puts a cut mesh's vertices, edges and faces in an order that follows
            from its shape alone. Blender's cutting leaves them in an order that
            changes from one run to the next, and with it the bytes of every
            file holding a sliced mesh: the same level exported twice must give
            the same files, or each export is a 50 MB change to the repository.
            """
            def order(elements, key):
                # bmesh sorts by a number only: number them in the order wanted, then sort by that.
                for index, element in enumerate(sorted(elements, key=key)):
                    element.index = index
                elements.sort()

            order(mesh.verts, lambda v: (v.co.x, v.co.y, v.co.z))
            order(mesh.edges, lambda e: tuple(sorted(v.index for v in e.verts)))
            order(mesh.faces, lambda f: tuple(sorted(v.index for v in f.verts)))

        def finish(mesh, name, centre):
            """bmesh -> object, with the hidden lip and seamless normals applied."""
            top_position = {}  # where a lip's lowered vertex came from, by where it is now
            settle(mesh)
            if skirt > 0:
                border = [edge for edge in mesh.edges if edge.is_boundary]
                if border:
                    extruded = bmesh.ops.extrude_edge_only(mesh, edges=border)["geom"]
                    for item in extruded:
                        if isinstance(item, bmesh.types.BMVert):
                            top = item.co.copy()
                            item.co.z -= skirt
                            top_position[item.co.to_tuple()] = top
                    for item in extruded:
                        if isinstance(item, bmesh.types.BMFace):
                            item.normal_update()
                            middle = item.calc_center_median()
                            if item.normal.dot(Vector((middle.x - centre.x, middle.y - centre.y, 0.0))) < 0:
                                item.normal_flip()
                            item.smooth = smooth
            if top_position:
                settle(mesh)
            normals = [source_normal(top_position.get(v.co.to_tuple(), v.co)) for v in mesh.verts] if smooth else None
            data = self.new_mesh(name)
            mesh.to_mesh(data)
            for material in obj.data.materials:
                data.materials.append(material)
            if normals:
                data.normals_split_custom_set_from_vertices(normals)
            piece = self.new_object(name, data, obj)
            if collider_only:
                piece["collider_only"] = True
            return piece

        first_x, last_x = math.floor(lo.x / size), math.floor((hi.x - 1e-6) / size)
        first_y, last_y = math.floor(lo.y / size), math.floor((hi.y - 1e-6) / size)

        for cx in range(first_x, last_x + 1):
            strip = source.copy()
            cut(strip, 0, cx * size, True)
            cut(strip, 0, (cx + 1) * size, False)
            for cy in range(first_y, last_y + 1) if strip.faces else ():
                piece = strip.copy()
                cut(piece, 1, cy * size, True)
                cut(piece, 1, (cy + 1) * size, False)
                if not piece.faces:
                    piece.free()
                    continue

                piece_lo = Vector((min(v.co.x for v in piece.verts), min(v.co.y for v in piece.verts), min(v.co.z for v in piece.verts)))
                piece_hi = Vector((max(v.co.x for v in piece.verts), max(v.co.y for v in piece.verts), max(v.co.z for v in piece.verts)))
                centre = Vector(((cx + 0.5) * size, (cy + 0.5) * size, 0.0))
                name = f"{obj.name}__{cx}_{cy}"
                chunk = self.chunk((cx, cy))
                self.grow(chunk, piece_lo, piece_hi)

                wants_far = self.lod and not collider_only and not truthy(obj.get("lod_skip", False))
                full = None
                settle(piece)  # before the snapshot: thinning it out depends on the order too
                if wants_far:
                    full = self.new_mesh(name + "__full")  # snapshot before the lip is added
                    piece.to_mesh(full)

                chunk["lods"][0].append(finish(piece, name, centre))
                piece.free()

                if full is not None:
                    triangles = triangle_count(full)
                    ratio = float(obj.get("lod_ratio", self.opts.lod_ratio))
                    reduced = full if triangles <= 32 else self.decimated(full, max(ratio, 16 / triangles))
                    far = bmesh.new()
                    far.from_mesh(reduced)
                    chunk["lods"][1].append(finish(far, name + "__far", centre))
                    far.free()
            strip.free()
        source.free()

    # --- Writing files ---------------------------------------------------------

    def export_glb(self, objects, path):
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj in objects)
        bpy.ops.export_scene.gltf(
            filepath=path,
            export_format="GLB",
            use_selection=True,
            export_yup=True,
            export_apply=True,
            export_extras=True,
            export_normals=True,
            export_tangents=False,
            export_materials="EXPORT",
            export_vertex_color="MATERIAL",
            export_animations=False,
            export_skins=False,
            export_morph=False,
            export_cameras=False,
            export_lights=False,
            export_draco_mesh_compression_enable=self.opts.draco,
        )
        triangles = 0
        for obj in objects:
            triangles += triangle_count(obj.data)
        return {"file": os.path.basename(path), "triangles": triangles, "bytes": 0}  # size: see write()

    def write(self):
        out = os.path.abspath(self.opts.out)
        os.makedirs(out, exist_ok=True)
        for stale in os.listdir(out):
            if stale.endswith(".glb") or stale == "map.json":
                os.remove(os.path.join(out, stale))

        textures = Textures(self.opts, out)
        files = []

        def export(objects, name, textured=True):
            path = os.path.join(out, name)
            files.append(self.export_glb(objects, path))
            if textured:
                textures.take_from(path)
            return files[-1]

        manifest_chunks = []
        map_lo = map_hi = None
        for (cx, cy), chunk in sorted(self.chunks.items()):
            if not chunk["lods"][0]:
                continue
            lods = [export(set(chunk["lods"][0]), f"chunk_{cx}_{cy}.glb")]
            far = self.merge_far(chunk["lods"][1], f"far_{cx}_{cy}") if self.lod else None
            if far is not None:
                lods.append(export({far}, f"chunk_{cx}_{cy}.far.glb", textured=self.cards is not None))
            lo, hi = bounds_to_game(chunk["lo"], chunk["hi"])
            manifest_chunks.append({"id": f"{cx}_{cy}", "min": lo, "max": hi, "lods": lods})
            map_lo = lo if map_lo is None else [min(a, b) for a, b in zip(map_lo, lo)]
            map_hi = hi if map_hi is None else [max(a, b) for a, b in zip(map_hi, hi)]

        global_entry = None
        if self.global_objects:
            global_entry = export(set(self.global_objects), "global.glb")

        textures.finish()
        for entry in files:  # sizes only now: finish() rewrites the files that use textures
            entry["bytes"] = os.path.getsize(os.path.join(out, entry["file"]))
        texture_entry = None
        if textures.files:
            texture_entry = {
                "count": len(textures.files),
                "bytes": sum(os.path.getsize(os.path.join(textures.folder, name)) for name in textures.files),
                "ktx2": textures.ktx2,
            }
        self.textured_files = len(textures.documents)

        # Blend masks: plain greyscale images beside the chunk files.
        blends = []
        for blend in self.blends:
            os.makedirs(os.path.join(out, "masks"), exist_ok=True)
            file_name = f"masks/{bpy.path.clean_name(blend['mask'].name)}.png"
            copy = blend["mask"].copy()
            copy.filepath_raw = os.path.join(out, file_name)
            copy.file_format = "PNG"
            copy.save()
            bpy.data.images.remove(copy)
            blends.append({"material": blend["material"], "layer": blend["layer"], "mask": file_name, "map": blend["map"]})

        heights = self.bake_heights(out) if self.height_source is not None else None

        blend_name = os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "map"
        manifest = {
            "version": MANIFEST_VERSION,
            "name": self.opts.name or blend_name,
            "chunkSize": self.opts.chunk,
            "min": map_lo or [0, 0, 0],
            "max": map_hi or [0, 0, 0],
            "global": global_entry,
            "textures": texture_entry,
            "markers": self.markers,
            "blends": blends,
            "heights": heights,
            "chunks": manifest_chunks,
        }
        with open(os.path.join(out, "map.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, separators=(",", ":"))
        return manifest

    def cleanup(self):
        """Leave the .blend exactly as it was (matters when run from Blender's UI)."""
        for mesh, name in reversed(self.added_uv_layers):
            layer = mesh.uv_layers.get(name)
            if layer is not None:
                mesh.uv_layers.remove(layer)
        for obj in list(self.work.objects):
            bpy.data.objects.remove(obj)
        bpy.data.collections.remove(self.work)
        for mesh in self.temp_meshes:
            try:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass
        if self.far_material is not None and self.far_material.users == 0:
            bpy.data.materials.remove(self.far_material)


def main():
    opts = parse_args()
    started = time.time()
    exporter = Exporter(opts)
    try:
        exporter.collect()
        manifest = exporter.write()
    finally:
        exporter.cleanup()

    chunks = manifest["chunks"]
    full = sum(c["lods"][0]["triangles"] for c in chunks)
    far = sum(c["lods"][1]["triangles"] for c in chunks if len(c["lods"]) > 1)
    size = sum(lod["bytes"] for c in chunks for lod in c["lods"]) + (manifest["global"]["bytes"] if manifest["global"] else 0)
    textures = manifest["textures"]
    if textures:
        size += textures["bytes"]
        print(
            f"[export_map] {textures['count']} textures, {textures['bytes'] / 1e6:.2f} MB "
            f"{'as KTX2' if textures['ktx2'] else 'uncompressed'}, shared by {exporter.textured_files} chunk files"
        )
    print(
        f"[export_map] {manifest['name']}: {len(chunks)} chunks of {opts.chunk:g} m, "
        f"{full:,} triangles full / {far:,} far, {len(manifest['markers'])} markers, "
        f"{size / 1e6:.1f} MB -> {os.path.abspath(opts.out)} ({time.time() - started:.1f}s)"
    )


if __name__ == "__main__":
    main()
