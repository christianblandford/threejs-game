# Maps from Blender

A level is built in Blender and exported into `public/maps/`. Everything
needed is in this repository: the exporter and the scripts that build the
test levels (`tools/blender/`), the tools that fetch textures and models
(`tools/`), and the sources they use (`maps/textures/`, `maps/models/`).
`docs/level-building.md` is the step-by-step guide to putting a level
together; this is the reference.

```bash
npm run map:export -- maps/my_map.blend     # -> public/maps/my_map/
npm run map:make-town                       # builds maps/test_town.blend by script, then exports it
npm run map:make-hall                       # the town hall's inside -> public/maps/test_town/interiors/hall/
npm run map:make-test                       # an open island (not one of this game's levels yet)
npm run texture:get -- cobblestone_floor_08 # a Poly Haven surface -> maps/textures/<id>/
npm run model:get -- shrub_03               # a Poly Haven model   -> maps/models/<id>/
```

An export writes a `map.json` manifest, two small `.glb` files per chunk
(full detail, and a merged far version), `global.glb` for what is always
loaded, and the map's textures in `textures/` as KTX2. Add the level to
`LEVELS` in `src/config.ts` (an interior goes in its level's `interiors`)
and reload. Blender is found automatically on macOS and Windows; otherwise
set `BLENDER=/path/to/blender`. It needs `npm install` to have been run.

The town takes a few minutes to build and export; the hall under a minute.
Encoded textures are kept between exports, so only new or changed ones take
time. The same level gives the same files, byte for byte: nothing changes
in `public/maps/` unless something in the level did, so a diff there is a
real change. (Blender's cutting leaves a sliced mesh in an order that
differs from run to run; `settle()` in the exporter puts it in one that
follows from its shape. Anything new that cuts or merges meshes has to do
the same, and the test is to export twice and compare.)

## How it stays fast

- The world is cut into a grid (64 m by default, `--chunk N`; the town uses
  32). Only chunks within the view distance are in memory.
- Nearby chunks load at full detail with collision and shadows. Distant ones
  load a far version: everything in the chunk thinned and merged into one
  mesh with one material, a single draw call.
- Big meshes such as terrain are sliced along chunk borders, with normals
  carried over so the cuts do not show.
- Linked duplicates (Alt+D) are pooled: one mesh for the whole map, drawn as
  one instanced batch however many chunks they span. Use them for trees,
  rocks, props: anything repeated.
- Materials of the same name are shared across chunks, and textures are
  written once per map, so each is fetched and held once.

## Conventions in the .blend

| | |
|---|---|
| Empty named `spawn` | where the player starts (its -Y axis is "forward") |
| Any other Empty | a marker: `map.marker("name")` gives its position |
| Empty with an `fx` property | fire or smoke that runs while the player is near: `campfire`, `brazier`, `smoke` (optional `fx_scale`) |
| Empty with a `trigger` property | a zone, named by the property's value. Its volume is the Empty as drawn: display it as a Cube or Sphere and scale it to fit (it can turn about the vertical axis, not tilt) |
| Empty with a `door` property | a door to another place: `door = "hall#entrance"` is the marker `entrance` in the interior whose id is `hall` (`door = "back"`: wherever the player came in from). Optional `prompt` and `radius`. With `walk = true` it is a zone that takes whoever walks in |
| Empty with a `talk` property | someone to talk to: `talk = "steward"` plays `src/game/stories/steward.ink` (`"steward#knot"` starts at another knot). Optional `prompt` and `radius`; `npc` stands a stand-in character there |
| Empty with an `interact` property | something the player can use, named by the value. What it does is `src/game/interactions.ts`. Optional `prompt` and `radius` (default 2 m) |
| Empty with `kind = "portcullis"` | where this game's gate stands (`src/game/gate.ts`) |
| Object property `ground_cover` | grass is planted on this object. Paint where in Weight Paint mode, in a vertex group named `ground_cover` (1 thick, 0 bare); without the group it grows everywhere |
| Material named `Water` or `Sea` | drawn as water. `Water` is clear enough to see the bottom; `Sea` is opaque |
| Collection named `Colliders...` | invisible, collision-only geometry |

Per-object custom properties (Object Properties > Custom Properties):

| Property | Values | Effect |
|---|---|---|
| `collision` | `mesh` `box` `cylinder` `sphere` `none` | what stops the player (default `mesh`; `box` for linked duplicates). `sphere` is treated as an upright cylinder, and `convex` as a box |
| `collision_radius` | metres | radius for `cylinder` |
| `collider_only` | true | invisible, collision only |
| `shadows` | false | does not cast shadows |
| `always_loaded` | true | never unloads (water, skyline) |
| `split` | true / false | force or forbid slicing along chunk borders |
| `skirt` | true / false | force or forbid the hidden lip hung under the edges of each sliced piece, which covers gaps between a chunk drawn in detail and a neighbour drawn far |
| `lod_ratio` | 0 to 1 | triangles kept in the far version |
| `lod_skip` | true | left out of the far version |
| `far_mesh` | a mesh's name | that mesh stands in for this object far away (crossed picture cards for a tree made of leaf cards) |
| `ground_cover` | a name, e.g. `grass` | grass is planted on it (see above) |
| `blend_layer`, `blend_mask` | a material's name, an image's name | a second material blended into the object's own where the image is white: a path worn into grass |
| `height_map` | true | this object is the lie of the land; its height is baked into an image the water reads to tell shallows from depths. Put it on the terrain |

Per-material custom properties (Material Properties > Custom Properties):

| Property | Values | Effect |
|---|---|---|
| `metres` | number | how much ground one repeat of the texture covers (`surface()` in `tools/blender/surfaces.py` sets it). Marks a tiled surface: `src/engine/surface.ts` |
| `variation` | 0 to 1 | strength of the broad light and hue patches that hide tiling (default 0.55) |
| `detile` | 0 to 1 | mixes in a shifted and a much larger copy of the texture, the larger taking over with distance. For ground and other organic surfaces |
| `wind` | number | the material's plants sway; the value scales how much. The mesh needs a second UV map holding sway weight and phase (`tools/blender/plants.py` writes it) |
| `matte` | true | almost no mirror-like reflection (scanned plants) |
| `gain` | number | multiplies the material's colour |
| `foliage` | true | leaf cards: matte, no anisotropic filtering |

The code that reads these is `sort()` in `src/engine/map/index.ts` (objects),
`upgradeMaterial()` in `src/engine/materials.ts` (materials), and
`src/engine/interact.ts` and `src/engine/effects.ts` (markers). A level can
also lay its own values over a material's from `src/config.ts`.

## Coordinates

The glTF files are right-handed, as three.js is, and are used as they are.
The numbers in `map.json` (markers, chunk bounds, the path mask's and the
height image's mapping) are written mirrored in x, a convention shared with
the Babylon scaffold this exporter came from: the loader negates x
(`markerPosition`, `markerYaw` in `src/engine/spaces.ts`). The same exported
map loads in both projects. The exporter here has moved on from that
project's in one way: its exports repeat exactly (above).

## Limits to know about

- Far versions carry no textures: a textured surface gets its texture's
  average colour.
- A tree's far stand-in is a few flat cards showing a picture of it
  (`far_mesh`, made by `tools/blender/plants.py`); the pictures are one
  atlas carried by the far version's material (`impostor` on a material).
- Textures are capped at 2048 px (`--max-texture-size N`).
  `--texture-quality 1-255` trades size for quality; `--no-ktx2` turns
  compression off. Normal maps are stored at half size by default
  (`--normal-scale 1` keeps them whole): they are the one kind the compact
  format visibly damages, so they are kept in the high-fidelity one.
- The KTX2 encoder is Basis Universal's, taken from the package
  `@babylonjs/ktx2encoder` because that is the one build of it that runs
  under Node. Nothing else of Babylon's is used; the game decodes with
  three's own transcoder (`public/basis/`, kept in step by the export).
- Each object costs one draw call per material in the main view, so join
  neighbouring buildings into one object, as the test town does. (Shadows
  do not pay this: each chunk casts as one merged mesh.)
- Maps are static geometry: no animation, armatures or lights are exported.
  Spawn what moves from code, at markers.
- Fetching new texture sets or models downloads from Poly Haven (CC0).
