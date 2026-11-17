# Lessons

What building this on three.js r187 taught, by topic: what the engine
gives and what had to be written, and the traps that cost real time. Read
it before starting a game on this; add to it when something costs a day.

## What is three.js's own, and what is not

The rule here is the Babylon project's: what the engine ships first, then
the library everyone recommends, and code of our own only where neither
exists.

- **From three.js and its add-ons:** the WebGPU renderer and node materials,
  `RenderPipeline` and every screen effect (`SSGINode`, `GTAONode`,
  `DenoiseNode`, `TRAANode`, `BloomNode`), `CSMShadowNode`, `SkyMesh`,
  `PMREMGenerator`, `GLTFLoader` and `KTX2Loader`, `InstancedMesh`,
  `AnimationMixer`, `DynamicLighting`, `PointerLockControls` (mouse look),
  `Timer` (frame time), `AudioListener` and `Audio`.
- **Recommended libraries:** Rapier for physics, including its own character
  controller; recast-navigation-js for navigation; inkjs for dialogue.
- **Written here, because three.js has nothing:** map streaming, grass,
  the particles (as node-material sprites), doors and interiors, the saved
  world, the things to use, menus and HUD (HTML).

About `three/addons/physics/RapierPhysics.js`: it wraps the same Rapier
package and version used here, but it is a helper for demos. It imports
Rapier from a CDN at run time, and it steps the world by itself on a
real-time timer, so the world cannot be paused or stepped by the game's own
clock. The game therefore uses Rapier directly (`src/engine/physics.ts`).

## Traps worth knowing

**Moving vertices in a shader makes everything that moves vibrate, unless
the previous position is moved too.** three's motion vectors compare where
a vertex is now with where it was, and "where it was" is the untouched
mesh. A `positionNode` that only moves the current position (wind, here)
makes every swaying leaf appear to jump by its whole sway each frame;
temporal anti-aliasing then discards its history for those pixels and the
picture never settles, with camera and wind both still. `displaced()` in
`src/engine/materials.ts` adds the same offset to `positionPrevious`. Found by
measuring: `bench.flicker(16, true)` in the console draws a run of frames
from a still camera and shows which pixels change. Trees and grass went
from 16% of pixels changing to under 5%.

**Screen-space bounced light is grainy, and three things each made it
worse.** It takes a few random samples per pixel and relies on temporal
anti-aliasing to average them. What is left shows as grain that crawls:
dark mottling on anything of one flat colour (an awning, plaster), and
orange sparks on the wall beside every lamp.

- *A plain sharpening pass afterwards* more than doubles whatever difference
  is left between frames. It was removed, and later put back in its
  noise-aware form once the smoothing below was in (see "Three defaults"
  further down).
- *No spatial smoothing.* `DenoiseNode` over both of the effect's results,
  across neighbouring pixels on the same surface, is what clears flat
  colours. About 3 ms.
- *Very bright small things in the picture it samples* (a lamp at seven
  times sunlit white): the few samples that land on one light their pixels
  like sparks. The picture it gathers from is capped at about sunlit white.

Measured on a sunlit view with the camera still (share of pixels moving by
more than 8 brightness levels over 20 frames): 7.7% as first built; 1.3%
without sharpening; 0.3% with smoothing as well, which is the same as with
no bounced light at all. `?denoise=0` and `?giSourceLimit=1000` each put
one of them back.

**Bloom needs a ceiling as well as a threshold.** The sky model's sun disc
is thousands of times brighter than a lamp. With only a threshold, a glow
faint enough for lamps still veils the whole picture in white whenever the
sun is in view. What is fed to the bloom is capped (`?bloomCap=`), and the
sky's haze is kept low and tight around the sun, or a third of the sky
toward it is glare with no bloom at all.

**Timing three by hand.**

three redraws the scene once per tick of its own animation loop. Call
`render()` twice in one tick and the second call redraws only the final
full-screen quad. A benchmark loop of hand-made frames therefore measures
almost nothing (the first numbers here said the bare scene took 2.7 ms)
unless it advances the tick itself, as `drawByHand` in `src/engine/renderer.ts` does.
Counting the GPU's draw calls per frame is what caught it.

### Three defaults that made the picture soft and the sky white

Found by critics who had not built it (see "Judged by fresh critics"
below), not by looking at it ourselves for a day.

- *Textures are not filtered along the line of sight unless asked.* three's
  default anisotropy is 1. Ground and walls are mostly seen at a shallow
  angle, and cobbles turned to a flat colour ten metres from the camera.
  Map textures now get 8 (`upgradeMaterial`). Fine detail in the stills rose
  10 to 25%.
- *Temporal anti-aliasing takes away more than half of the fine detail.*
  Measured on a still of the road: 88 with it off, 39 with it on (mean
  absolute Laplacian of brightness; the Babylon still measures 119).
  Bounced light and the other effects made no difference. `SharpenNode`
  straight after it, with its `denoise` option on, brings it to 68 and
  leaves frame-to-frame flicker where it was (0.02% of pixels against
  0.03%), for 0.6 ms. Plain sharpening, earlier, doubled the flicker and
  was taken out: the option is what makes it usable.
- *The sky model is physical and far brighter than the town it lights.*
  Looking toward the sun, 12% of the frame was pure white. Easing its
  brightness off at the top stopped the clipping but left a pale blank,
  because sky and cloud were pressed against the same ceiling. Turning the
  sky down first and then easing it keeps cloud brighter than sky: 0.1% of
  the frame clipped, sky saturation 0.35 from 0.05. Only what is seen is
  changed; the light the town gets is baked from another copy of the sky.

### Shade with no form in it

A critic judging stills against a shipped game named this first: house
fronts out of the sun were one flat murky tone. Four things were behind it.

- *The bounced-light effect's own occlusion does nothing local.* It reaches
  eight metres, to carry light across a street. With the whole effect off,
  the picture was the same, only 7% brighter everywhere: a dimmer, not shade
  in corners. A second, short-reach occlusion (`GTAONode`, 1.2 m, raised to
  the power 2) is what darkens the foot of a wall and the underside of an
  eave.
- *That occlusion flickers unless it is smoothed.* Raised to a power, its
  grain put ten times as many pixels in motion as the rest of the picture
  (0.3% against 0.03%). Smoothing it by itself cost 4.8 ms a frame. The
  two occlusions are instead multiplied into one texture and smoothed
  together, by the pass the bounced light already pays for: 1.7 ms, and the
  flicker back at 0.04%.
- *The sky model carries on below the horizon as more sky.* So everything in
  shade was lit from below as brightly and as coolly as from above. A warm
  disc stands in for the sunlit ground in the baked sky light. Measured, the
  sky it replaces is worth about 5: at 5 only the colour changes, at 1.5
  shaded walls go a fifth darker. It is set to 6.
- *Shadow edges were a ruled line.* `shadow.radius` 3 (texels) softens them
  and costs nothing measurable.

Exposure went from 0.66 to 0.78 with it, so that shaded fronts are filled
and sunlit stone is nearer white; 0.88 gained seven levels at the bright
end and cost the sky a sixth of its colour.

### Grass darker than the ground it grows on

Two critics measured the blades 45 levels darker than their ground, and in
town near black. The blades' normals all point up, so that a field is lit
as the ground is and not blade by blade; but the material is two-sided,
and three.js turns the normal round on any face seen from behind. Half the
blades were being lit from below. Give such a material its own
`normalNode` (here simply "up", in view space): what a material supplies
there is used as it is, for both faces.

Four more things the same rounds settled, each against a blind critic:

- **Thin cover should be short grass, not a few tall tufts.** Planting
  fewer tufts where the paint is thin left town lawns with a dozen clumps
  on bare ground. The count now falls off slowly with the paint (its 0.35
  power) and the height takes the rest.
- **Much of a size.** A first version had sprigs a twentieth of the fox's
  height with the odd clump as tall as it: "weeds on bare ground". Blades
  now run 10 to 20 cm, 1 cm wide. A blade is never drawn narrower than a
  pixel and a half, or a field of them shimmers (0.17% of pixels changing
  with the camera and the wind both still, against 0.03% for bare ground).
- **A narrow threshold on a mask shows the mask's pixels.** The path edge
  was first sharpened by narrowing the blend, and came out stair-stepped:
  the mask is 37 cm a pixel. It is now read four times around each point,
  and the edge is broken by two sizes of noise and by the earth's own
  texture instead.
- **Darkening the grass ground made it olive**, and a blind critic preferred
  it as it was. The pale earth of the paths is what washed out in this
  renderer's brighter sun, and only that is held down (`materials` in
  `src/config.ts`).

Most of the grass drawn is the distant thin sward, not the thick one at
the player's feet (70,000 tufts against 23,000 in a meadow). One triangle
a blade there took the grass from 1.67 to 0.96 million triangles with no
measurable change to the picture.

### An effect written into the picture's shader runs at the picture's size

`denoise(...)`, like most of three's effect nodes that are not passes, is
a piece of shader: used in the final picture it runs once per pixel of the
canvas, whatever size its input was worked out at. Two of them cost more
than the effect they were cleaning. Wrap such a node in
`rtt(node, null, null, { resolutionScale: 0.5 })` to give it a texture of
its own at the size it deserves.

### A shadow pass draws every caster again, per cascade

three.js has no cheap path for shadows: each caster is a full draw call in
each cascade that reaches it, with its own bindings. Count them
(`renderer.info.render.drawCalls` with and without the sun's `castShadow`)
before believing shadows are a GPU cost: here they were three quarters of
the frame's draw calls and two thirds of its script time. A pooled prop
drawn as one `InstancedMesh` across the map is never culled from a
cascade, either: its bounding sphere is the map.

### What makes streaming stutter is objects that are new to the renderer

Measured on a flight across the town at 12 m/s (`bench.run`, 1280x800,
every frame timed with the GPU's share): the loader's own work when a chunk
arrives (colliders, grass, sorting) was never more than 8 ms, and the game
still dropped 67 frames to over 50 ms, the worst to 183 ms. All of that was
CPU time inside the draw. three.js prepares every object the first time
each pass draws it (the picture and each of four shadow cascades), a
fraction of a millisecond per object per pass, and the loader was throwing
away and remaking all hundred-odd instanced meshes of pooled props whenever
any chunk changed. Rewriting them in place (and making a larger one only
when one is outgrown) left 1 frame over 50 ms, the worst 53 ms, the 99th
percentile at 28 ms from 155 ms.

Two things that follow. Never rebuild what can be updated. And switching a
new chunk's meshes on a few per frame, to spread that first-draw cost, was
tried and was worse (156 frames over 33 ms against 2): a frame with anything
new in it pays a cost that is mostly not per mesh, so fewer such frames win.

Comparisons like these are only worth anything at a fixed canvas size, back
to back: the same flight measured twice an hour apart, in a window that had
changed size, differed by a factor of two.

With navigation and the foxes running, the same flight has 28 frames over
33 ms (of 1,242) and a 99th percentile of 40 ms: each chunk that arrives
has the walkable area re-planned around it, 4 to 17 ms in one piece, and a
tile in a built-up street can take 16 ms to build. That planning is the
next thing to spread over frames. The first build after loading (about a
thousand tiles) takes some fifteen seconds in the background at a tile or
so per frame.

### Thin smoke over a bright surface does not show

The picture is tone-mapped on its way to the screen, which squeezes its
bright end: smoke that covers a quarter of a sunlit pavement darkens it on
screen by a few per cent. The explosion's smoke was drawn correctly for an
hour and could not be seen. Smoke meant to be seen against lit ground wants
to be nearly opaque at its thickest; against the sky far less will do.

### A pass keeps the buffers it was first given

Changing graphics quality rebuilds the chain of effects (`compose` in
src/engine/picture.ts) and that works, with one condition: the scene pass must go on
writing the same set of buffers. Asked to write fewer than before (no
surface directions on "low", say), three.js still has the old ones on the
pass, looks for what should be written to them while compiling each
material, and throws: a black screen. So every quality level writes all
four, used or not.

### The loader may hand a mesh a copy of your material

Water is found by its material, which the map supplies. Comparing a mesh's
material with the one supplied (`mesh.material === water`) missed the moat:
`GLTFLoader` gives a mesh a clone when the mesh has something the material
must know about (vertex colours, no tangents). The moat was then given a
collider like any ground, and could be walked on. Mark the material
(`userData`, which a clone keeps) and test the mark.

### Particles do not belong in the main pass

The main pass writes more than colour: surface direction and movement, one
value per pixel, for the bounced light and the anti-aliasing. Those buffers
do not blend, so a transparent particle drawn there stamps its whole square
into them. Fire and smoke are therefore a scene of their own, drawn in a
second pass and laid over the picture after the anti-aliasing and before
the glow. Hiding behind walls is then done by hand, by comparing each
pixel's distance with the main pass's depth. Read that depth at `screenUV`
yourself: the pass's ready-made `getViewZNode()` samples with the
material's default texture coordinates, which on a sprite are the sprite's
own, and each particle then shows a tiny picture of the whole room.

### Lights that come and go

With three's ordinary lighting, the number of lights is part of every
material's shader: a fire lit or put out recompiles all of them.
`renderer.lighting = new DynamicLighting(...)` (an add-on) turns point
lights into a list the shader loops over, whose length is free. Two things
to keep: one point light that is always in the scene (giving no light), so
the first real one is not the change that recompiles everything; and no
shadows from these lights (a shadow-casting light goes back to the ordinary
path).

## Runs that did not repeat

The game is meant to come out the same from the same seed and inputs. A
reviewer reading the code found most of what follows; running one script
twelve times found the rest. None of it showed in a short run.

### The same run landed on one of two hashes

A 550-tick walk gave one of two results, about evenly, differing by a few
millionths of a metre in one fox. Three causes, each enough alone:

- **The world was loaded around the camera.** The camera eases to its
  place by the screen's clock, so where it was when navigation chose which
  tiles to build first varied. Load around the player.
- **Foxes were stood on last run's ground.** After a restart the map
  drops what the last run's player walked past, and the walkable area is
  rebuilt a moment later. A fox added in that moment was put on ground
  about to go; added after, on none. Wait for the rebuild first
  (`navigation.settled()`).
- **Foxes were added as their models finished loading**, so which was
  which to the crowd depended on the network. Load all, then add in order.

How it was found: trace one value per tick in two runs that disagree, find
the first tick that differs, then look at what that value hides. The foxes'
positions first differed at tick 540; their hidden steering state differed
at tick 0.

### A conversation changed the saved world by the clock

Ink seeds each new story from the time of day and keeps the seed in the
story's memory, which is saved. Two runs alike in every way had different
saved worlds after one conversation. `createDialogue` now takes the seed
from the game's own dice.

### Rapier does not forgive a call on a removed body

A door trip still fading when the game was restarted went on to teleport
the old game's player. That throws inside Rapier, and every Rapier call
after it throws "recursive use of an object": the page is dead. Anything
that waits (a fade, a load) checks afterwards that its game is still there.

### A collider counts from the next step, not from when it is added

Rapier's rays and character controller know of a collider only once the
world has stepped. The first tick after a chunk arrived, or after the
player was put somewhere, was played against the world as it had been.
`physics.refresh()` is a step of no time; the tick starts with one.

### What a snapshot left out

Found by asking of each piece of state, "would the next tick go
differently without it?": the conversation in progress, the way back out
of a room, which chunks of the level are solid (that depends on where the
player has been, not only where they are), and the wheel's zoom, which
was changed straight from a DOM event and never passed through a tick.
