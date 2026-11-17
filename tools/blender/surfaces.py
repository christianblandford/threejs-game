"""
Materials from the texture sets in maps/textures/ (fetched with
`npm run texture:get -- <id>`, see tools/textures.mjs).

    from surfaces import surface
    cobbles = surface("Cobbles", "cobblestone_floor_08")
    rose = surface("PlasterRose", "white_plaster_02", tint=(0.9, 0.62, 0.55))
    ground = surface("Ground", "grass_ground", vertex_colors="Col")

A set is three images, wired up the way Blender's glTF exporter understands,
which is also how to do it by hand in the Shader Editor:

    color.jpg   -> Base Color (through a Multiply mix with a colour, to tint it)
    normal.jpg  -> Normal Map node -> Normal            (image set to Non-Color)
    arm.jpg     -> Separate Color: Green -> Roughness, Blue -> Metallic,
                   Red -> the "Occlusion" input of a node group named
                   "glTF Material Output"               (image set to Non-Color)

The images are referenced, not packed into the .blend, so one set on disk
serves every map that uses it.

`surface` returns None when the set isn't there, so a script can fall back to
something plainer: `surface(...) or material(...)`. The material remembers how
much ground one repeat of the texture covers, in metres, as `mat["metres"]`;
lay UVs out at that size and the surface appears at its real scale.
"""

import json
import os

import bpy
import numpy as np

TEXTURES = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "maps", "textures"))

_images = {}


def _image(set_id, name, color):
    path = os.path.join(TEXTURES, set_id, f"{name}.jpg")
    key = (path, color)
    if key not in _images:
        image = bpy.data.images.load(path, check_existing=True)
        if not color:
            image.colorspace_settings.name = "Non-Color"
        _images[key] = image
    return _images[key]


def _occlusion_group():
    """The node group the glTF exporter looks for to find a material's ambient occlusion."""
    name = "glTF Material Output"
    group = bpy.data.node_groups.get(name)
    if group is None:
        group = bpy.data.node_groups.new(name, "ShaderNodeTree")
        group.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
        group.nodes.new("NodeGroupInput")
    return group


def available(set_id):
    return os.path.isfile(os.path.join(TEXTURES, set_id, "color.jpg"))


def _recoloured(set_id, tone=None, even=0.0):
    """
    The set's colour map reworked, keeping its fine detail.

    `tone`: shifted so that its average colour is this (linear RGB). Unlike a
    tint, this can lighten as well as darken: a brown forest floor becomes a
    pale meadow with the same texture.

    `even` (0 to 1): how much of its broad light and dark is taken out. A
    photograph of a wall has pale and dark patches a metre or two across, and
    those, not the stones, are what the eye catches coming round again when
    the picture repeats. Evened out, the picture repeats unnoticed, and the
    patches are painted back by something that does not repeat (vertex
    colours, the game's own variation).
    """
    source = _image(set_id, "color", True)
    name = set_id + ("_" + "_".join(f"{c:.3f}" for c in tone) if tone is not None else "") + (f"_even{even:.2f}" if even else "")
    if name in bpy.data.images:
        return bpy.data.images[name]
    width, height = source.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    source.pixels.foreach_get(pixels)  # as stored in the file: display-encoded (sRGB), not linear light
    stored = pixels.reshape(height, width, 4)[:, :, :3]
    rgb = np.where(stored <= 0.04045, stored / 12.92, ((stored + 0.055) / 1.055) ** 2.4)  # linear light
    if even:
        # The broad part: the picture blurred to about a twentieth of its
        # width, wrapping round its edges so it still tiles. Dividing by it
        # (rather than subtracting) keeps dark stones dark in a pale patch.
        fy, fx = np.fft.fftfreq(height)[:, None], np.fft.rfftfreq(width)[None, :]
        blur = np.exp(-2.0 * (np.pi * 0.05) ** 2 * ((fy * height) ** 2 + (fx * width) ** 2))
        grey = rgb.mean(axis=2)
        broad = np.fft.irfft2(np.fft.rfft2(grey) * blur, s=grey.shape)
        rgb = rgb * ((grey.mean() / np.maximum(broad, 1e-4)) ** even)[:, :, None]
    rgb = rgb.reshape(-1, 3)
    if tone is not None:
        # The new light values are the picture's *display* values scaled to
        # the wanted average. That also softens its contrast (display values
        # are closer together than light is), which suits a recoloured
        # ground: a forest floor lifted to meadow green with its full
        # contrast kept comes out speckled with black.
        soft = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0.0) ** (1 / 2.4) - 0.055)
        rgb = soft * (np.asarray(tone) / soft.mean(axis=0))
    rgb = np.clip(rgb, 0.0, 1.0)
    stored = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)  # back to display values for an 8-bit file
    result = bpy.data.images.new(name, width, height, alpha=False)
    result.pixels.foreach_set(np.concatenate([stored, np.ones((len(stored), 1))], axis=1).astype(np.float32).ravel())
    result.pack()
    return result


def surface(name, set_id, tint=None, relief=1.0, vertex_colors=None, tone=None, even=0.0):
    """
    A material from a texture set. `tint` multiplies the colour (one plaster
    set, many colours of house); `relief` scales the strength of its normal
    map. `vertex_colors` names a colour attribute to multiply by instead:
    painted variation over a big surface such as terrain (lush here, dry
    there, bare earth where people walk). `tone` recolours the set so its
    average colour is the one given, lighter or darker, keeping its detail.
    `even` (0 to 1) takes out the set's own broad patches of light and dark,
    which are what shows when it repeats across a large wall.
    """
    if not available(set_id):
        print(f"[surfaces] no texture set '{set_id}' in maps/textures; run: npm run texture:get -- {set_id}")
        return None
    with open(os.path.join(TEXTURES, set_id, "info.json")) as handle:
        info = json.load(handle)

    result = bpy.data.materials.new(name)
    if result.node_tree is None:
        result.use_nodes = True
    nodes, links = result.node_tree.nodes, result.node_tree.links
    shader = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    x, y = shader.location.x, shader.location.y

    color = nodes.new("ShaderNodeTexImage")
    color.image = _image(set_id, "color", True) if tone is None and not even else _recoloured(set_id, tone, even)
    color.location = (x - 620, y + 120)
    if tint is None and vertex_colors is None:
        links.new(color.outputs["Color"], shader.inputs["Base Color"])
    else:
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        mix.location = (x - 300, y + 120)
        links.new(color.outputs["Color"], mix.inputs["A"])
        links.new(mix.outputs["Result"], shader.inputs["Base Color"])
        if vertex_colors is not None:
            painted = nodes.new("ShaderNodeVertexColor")
            painted.layer_name = vertex_colors
            painted.location = (x - 620, y + 360)
            links.new(painted.outputs["Color"], mix.inputs["B"])
        else:
            mix.inputs["B"].default_value = (*tint, 1.0)

    normal = nodes.new("ShaderNodeTexImage")
    normal.image = _image(set_id, "normal", False)
    normal.location = (x - 620, y - 480)
    bump = nodes.new("ShaderNodeNormalMap")
    bump.inputs["Strength"].default_value = relief
    bump.location = (x - 300, y - 480)
    links.new(normal.outputs["Color"], bump.inputs["Color"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])

    arm = nodes.new("ShaderNodeTexImage")
    arm.image = _image(set_id, "arm", False)
    arm.location = (x - 620, y - 180)
    split = nodes.new("ShaderNodeSeparateColor")
    split.location = (x - 300, y - 180)
    links.new(arm.outputs["Color"], split.inputs["Color"])
    links.new(split.outputs["Green"], shader.inputs["Roughness"])
    links.new(split.outputs["Blue"], shader.inputs["Metallic"])
    occlusion = nodes.new("ShaderNodeGroup")
    occlusion.node_tree = _occlusion_group()
    occlusion.location = (x, y - 420)
    links.new(split.outputs["Red"], occlusion.inputs["Occlusion"])

    result["metres"] = float(info.get("metres", 1.0))
    result["texture_set"] = set_id
    return result
