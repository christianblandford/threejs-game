# Building a level

How a level for this scaffold is put together, in the order that works. The
two test maps are the worked examples: `tools/blender/make_test_town.py` (a
dense town) and `tools/blender/make_test_map.py` (open country). Both are
scripts, so every choice in them can be read; a level built by hand in
Blender follows the same steps with the same conventions.

The split to keep in mind: **Blender says what is where; code says what it
does.** Geometry, materials, where grass grows, where a path runs, where a
zone or a usable thing or an effect sits: Blender. What the zone triggers,
what the lever opens, who walks about: `src/game/`.

## 1. The ground

One mesh, sculpted or generated, a vertex every metre or two. It gets:

- **Shape first.** Rises, hollows for ponds, a hill for the important
  building. Flat ground is the first thing that makes a level look made.
  Level a pad under each building rather than the whole street.
- **A ground texture tinted by vertex colours**:
  `surface("Ground", "grass_ground", vertex_colors="Col", tone=...)`, with
  the colours painted lush, dry, trodden, muddy. `detile = 1` on the
  material.
- **Where grass grows**: object property `ground_cover = "grass"` and a
  vertex group `ground_cover`, cleared under roads, buildings and water and
  thinned toward them.
- **Paths and bare earth as a mask, not as meshes**: `blend_layer` (a second
  material) and `blend_mask` (an image laid over the terrain from above).
  Lower the ground a hand's depth along the same lines. Paths drawn into one
  mask merge where they cross.
- **`height_map = true`**, so water knows its depth and softens at the bank.

Paved surfaces (cobbles, flagstones) are their own meshes draped over the
terrain, a few centimetres proud, with their own UVs at the texture's true
scale.

## 2. Surfaces

Every surface that matters gets a scanned texture set at its true scale
(`npm run texture:get`, `surface()` in `tools/blender/surfaces.py`). One set
serves many materials through `tint` and `tone`. What a set cannot do is make
a flat wall interesting: see the next section.

## 3. Buildings

A box with a texture on it is wallpaper, however good the texture. What
makes a house read as built is **relief that casts small shadows**, and
attention at the places the eye goes: the roof's edges, the openings, and
the line where wall meets ground. `house()` and the "Dressing the houses"
section of `tools/blender/make_test_town.py` are the worked example. From
the ground up:

- **Footing.** A stone plinth that takes up the slope, a course proud of
  the wall, and door steps with risers the player can walk up (0.15 m).
- **Walls with depth.** Openings are real holes with dark reveals and the
  glass set back 14 cm. Timbers stand 4 to 5 cm proud of the plaster, in a
  few patterns (square panels, close studding, saltires, braces). Masonry
  ground floors get quoins and lintels.
- **Jetties.** Upper floors step out 40 cm on a beam, joist ends and
  brackets. Nothing else gives a street as much shadow for as few triangles.
- **Roofs are the most-seen surface in a town** (from walls, hills, any
  third-person camera) and get the most care: a slab with thickness;
  eaves that overhang, with a board and rafter tails; barge boards on
  gables; ridge and verge tiles lapped; a modelled lip on each tile column
  at the eaves so the edge is scalloped, not ruled; dormers and cross-gables
  to break large planes; chimneys in several designs. Three coverings (new
  clay, old mossy tile, shingle) from two texture sets.
- **Fittings as linked duplicates.** Window frames with sill, mullions and
  transom; doors; shutters; flower boxes; hanging signs; wall lamps;
  awnings; chimney pots. Twenty-two small meshes placed 670 times cost the
  game almost nothing (they are drawn as a few static batches). Give them
  `collision = "none"`, and the flat ones `shadows = false`.
- **Vary by plan, not by chance.** Lay out each row before building it, so
  neighbours never share a plaster colour, nor both height and roof
  direction. Random choice per house produces twins side by side.
- **Join each row into one object** with as few materials as will do: the
  town's rows carry seven to nine.

Budget from the test town: about 3,200 triangles a house with all of the
above; 150,000 for 27 houses, a hall with tower, a well, stalls and a farm.

What it still lacks, for whoever takes it further: roof planes are flat
between their edges (a sag, or a few lifted tiles, would help from above);
cloth is flat colour; party walls and backs are plainer than fronts.

## 4. Big walls

A town wall, a cliff face, a castle: one surface seen whole, hundreds of
square metres of it. This is where a repeated texture shows worst, and the
town's wall (`tools/blender/make_test_town.py`, "Town wall") is the worked
example of the four things that fix it:

- **A texture photographed large.** `defense_wall_02` covers 12 m a repeat;
  a 2 m block texture on a 100 m wall is a grid whatever else is done.
- **Its own patches taken out** (`surface(..., even=0.8)`). What gives a
  repeat away is not the stones but the pale and dark areas a metre or two
  across. Without them the picture can repeat unnoticed.
- **Patches painted back that do not repeat**: vertex colours computed from
  position (`weather()`): lighter and darker stretches, damp and green at
  the foot, dark runs below the parapet, bleaching at the top. The faces are
  laid in metre-wide strips so there are vertices to paint.
- **A shape that breaks the plane.** A foot that leans out, a projecting
  course on corbels under the parapet, real battlements with caps, a tower
  every 25 m, corner stones, arrow slits, an arch with its ring of stones.
  Each casts a shadow and gives the eye something that is not the texture.

Split it in two objects: what can be walked on or into (with collision), and
the small stonework (`collision = "none"`, `shadows = false`, `lod_skip`).
The trim is two thirds of the triangles and none of the physics.

## 5. Water

A flat mesh at the water's level with a material named `Water` (clear: you
see the bottom, so model one) or `Sea` (opaque). `collision = "none"`. The
game replaces the material and the player swims where it is deep enough.

## 6. Plants

Trees and bushes come from `tools/blender/plants.py`, scanned ground plants
from `npm run model:get`. Rules that keep it fast:

- Build a handful of trees (three or four kinds, a couple of seeds each) and
  place each many times as **linked duplicates**. Vary them by turning and
  scaling the object, not by making more meshes.
- Give every tree and bush `far_mesh = plant.far.name`: far away it is then
  drawn as picture cards of itself, inside the chunk's one far mesh.
- Give ground plants `lod_skip`, `shadows = false` and `collision = "none"`.
- Trees get `collision = "cylinder"` with the trunk's radius, so the player
  walks under the crown.
- **Flowers are scans too** (`flower_gazania`, `flower_ursinia`,
  `dandelion_01`, `flower_empodium`): each file holds several plants, some
  in bloom, some not (`model_variants(id)`; pick the ones in bloom). Plant
  them in patches of five to twelve of mostly one kind, not singly: a
  flower alone in a field is invisible, a patch is a splash of colour. Cut
  to about 700 triangles each. Keep them near their real size (scale 1.5 at
  most): doubled, a rosette becomes a cabbage.
- Put plants where they would grow: bushes at a wood's edge and along walls
  and fences, ferns under trees, weeds at the foot of anything nobody mows.
  Uniform scatter reads as scatter.

## 7. What code refers to

Empties, named. `spawn`; `poi_*` for anything a character or a camera should
find; an Empty with `trigger` for a zone (its drawn size is the zone); with
`interact` (+ `prompt`, `radius`) for something used; with `fx` for an
effect. Anything that moves or has state (a gate, a door) is an Empty saying
where and how big, and code builds it.

## 8. Keeping it fast

- **Objects, not triangles, are the cost.** Each object is one draw per
  material, again in each shadow cascade. Join a block of houses into one
  object; keep the number of materials on it down (the same plaster and
  timber on every house in the block).
- **Everything repeated is a linked duplicate** (Alt+D): lamps, barrels,
  stalls, trees, fence panels.
- **Chunk size** follows density: 32 m for a town, 64 m for country.
- **Texture sets are downloads**: a set is about 0.65 MB compressed. Tint
  before fetching another.

## 9. Export and check

```bash
npm run map:export -- maps/my_level.blend
```

then add it to `LEVELS` in `src/config.ts` and walk it. Check, in this order:
the draw count in the corner (a town should stay under about a thousand);
shaded walls (if they are mud, the light is wrong, not the texture); the far
distance (do the stand-ins match?); water edges; and the same walk on
`?webgl` (WebGL 2 in place of WebGPU).
