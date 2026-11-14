# three.js game prototype

One question, answered with measurements: **would the Babylon game scaffold
([babylon-game-engine-starter](https://github.com/christianblandford/babylon-game-engine-starter))
run faster, at the same quality, on three.js?**

This draws that project's test town with three.js r187: the same exported
map files and the same textures. It began as a measuring rig (the numbers
are below), was pushed for looks, and is now being brought toward what the
Babylon project can do: see "Parity" below for how far that has got.

![The road to the town gate](docs/road.jpg)
![The market square](docs/square.jpg)
![The moat](docs/moat.jpg)

## Parity with the Babylon project

| | Babylon project | Here |
|---|---|---|
| Map streaming, pooled props, shadows, sky light | yes | yes |
| Ground paths, grass, wind, water shaded by its depth | yes | yes |
| Bounced light | no | yes |
| Player: walk, run, jump, steps, follow camera | yes | yes |
| First person, swimming | yes | yes |
| Collision with the map | yes (Havok) | yes (Rapier) |
| Fire, smoke, firelight; fires put out and relit | yes | yes |
| One-off bursts (dust, sparks, splash, explosion), rain | yes | yes |
| Other characters, navigation | yes | yes (Recast, as there) |
| Things to use (E), the gate and its lever | yes | yes |
| Trigger zones (walk-in areas) | yes (any physics body) | yes (the player only) |
| Doors and interiors (the town hall) | yes | yes |
| Travel between levels | yes | no (one level here) |
| Saved world, Continue and New game | yes | yes |
| Dialogue (Ink), the steward's errand | yes | yes |
| HUD: prompt, news line, conversation | yes | yes (HTML) |
| Title and pause menus, settings | yes | yes (HTML; "New game" and "Restart level" reload the page) |
| Sound | yes | yes (the same made-up effects; no sound files in either) |
| Gamepad | yes | yes (and it works the menus and conversations, which it does not there) |
| The island level | yes | no |

Doors, the saved world and dialogue follow the Babylon project's design and
its Blender conventions exactly (`door = "hall#entrance"`, `talk =
"steward"`, `interact = "gate_lever"`): the same exported maps drive both.
Measured here, through the hall's door and back: about a second the first
time (the hall starts loading as the player comes within 30 m of its door),
half a second after that.

Gamepad: left stick to move, right stick to look, A jump, X use, left
stick click or left trigger to run, right stick click for first person,
Start for the menu; in menus and conversations the direction pad picks and
A accepts, B goes back.

Keys: WASD, Shift to run, Space to jump, E to use, V for first person
(or zoom all the way in), Esc or P for the menu, R for rain, B for an
explosion ahead (to try it: the Babylon project has a debug panel for these). The moat can be swum.

To try it: walk up the hill to the hall, **E** at its door, talk to the
steward on the dais (E or Space for the next line, the number keys to
answer), lower the gate with the lever just inside the gatehouse, and go
back to him. Reload with `?continue` and you are where you were, with the
gate as you left it; `?newgame` forgets everything.

## What makes it look like this

- **Bounced light** (`SSGINode`): a sunlit wall lights the one opposite, and
  creases and corners are shaded, from one screen-space effect. This is the
  thing the Babylon version of the town lacks most.
- **A strong sun against a weak sky.** Sky light at about a fifth of what the
  sky model gives; sunlit stone several times brighter than stone in shade.
- **Temporal anti-aliasing** (`TRAANode`), which also averages away the
  bounced light's sampling noise, followed by noise-aware sharpening
  (`SharpenNode` with `denoise`) to give back the detail it softens.
- **The sky's own clouds**, also baked into the ambient light.
- **Water** for anything called Water or Sea: dark, smooth, rippled by two
  drifting normal maps, mirroring the sky.
- **Tiled surfaces broken up**, as the Babylon game's surface plugin does
  it: soft blotches of lighter and darker, warmer and cooler, a shifted
  copy of the texture shown in patches, and a much larger copy taking over
  with distance (the Blender materials' `variation` and `detile`).
- **The ground**: worn paths blended in through the map's mask image, with
  an edge that follows the earth's own texture and breaks into clumps and a
  ragged fringe, the earth's own relief where it shows, and a fine grain in
  both at the player's feet.
- **Grass** planted where the Blender author painted it: a thick sward of
  fine blades within 16 m, a thin one of broader blades out to 48 m, each
  tuft sinking into the ground toward the end of its range. Thin paint
  grows short grass, not a few tall tufts. Each blade takes the colour of
  the ground under it.
- **Wind** in leaves and grass, from the sway data the Blender plant tools
  store in each mesh. Slow and gentle: half the Babylon game's strength.
- Four shadow cascades, bloom, a vignette, ACES tone mapping, distance fog.

All of the material work (`src/materials.ts`, `src/grass.ts`) is written
once, as three's shader nodes, and runs on WebGPU and WebGL alike.

With all of that on, at the size of a full window on a Retina screen: 33 to
39 ms a frame (26 to 30 frames a second) on the test machine, varying from
run to run; 45 to 60 frames a second in a smaller window. The dearest part
by far is the bounced light with its smoothing, around 10 ms: `?gi=0` swaps
it for plain ambient occlusion (about 25 ms, 39 frames a second), and
`?ratio=1` drops Retina.

`?scale=0.75` draws fewer pixels and upscales them with three's `TAAUNode`.
It saves only a few milliseconds here and is not the default: it softens the
picture and does not average the bounced light's noise well, which shows as
grain crawling over everything in shade.

## The comparison

The numbers below were taken before any of that, with three's stock effects
matched by name to the Babylon project's "high" preset.

```bash
npm install
npm run dev        # http://localhost:5173
```

Click to capture the mouse, WASD to move, Shift to run, Space to jump, the
wheel to zoom, F to switch between walking as the fox and flying the camera.

## What was measured

Frame time with the GPU's share included (draw a frame, wait for the GPU to
finish, time the whole), in the market square looking north, at three canvas
sizes. Same machine and browser as the Babylon numbers (Apple GPU, Chrome,
WebGPU).

| Canvas | Babylon 9.36 | three r187 |
|---|---|---|
| 1.0 million pixels | 18.7 ms | 24.4 ms |
| 2.25 million pixels (a full window on a Retina screen) | 23.3 ms | 29.5 ms |
| 4.0 million pixels | 29.4 ms | 37.0 ms |

Where three's 29.5 ms goes, at 2.25 million pixels:

| | three r187 | Babylon 9.36 |
|---|---|---|
| The scene alone (no shadows, no effects) | 8.3 ms | about 15 ms |
| Four shadow cascades | 7.6 ms | about 4 ms |
| Ambient occlusion, temporal anti-aliasing, bloom, sharpening | 13.7 ms | about 4.6 ms |
| **Whole frame** | **29.5 ms** | **23.3 ms** |

Of three's effects: temporal anti-aliasing 6.4 ms, bloom 3.6 ms, ambient
occlusion 2.8 ms. On three's WebGL 2 backend the whole frame is 29.3 ms.

## What that means

- **three's core is leaner.** The bare scene costs about half what
  Babylon's does, and under 2 ms of CPU for 220 draws.
- **Its stock shadows and effects are heavier** than Babylon's after that
  project's tuning: no per-cascade caster culling (600 shadow draws against
  420), and effect nodes that favour quality over cost.
- **So with the features matched by name, three is about a quarter slower
  today.** Tuned the way the Babylon project was (cascade culling, cheaper
  effect settings), it could plausibly reach 17 to 18 ms here.
- **The comparison flatters three.** The Babylon frame also draws grass,
  particles, characters and a HUD, steps physics, and runs that project's
  own material extensions (large-scale variation, de-tiling, blended paths,
  wind, water). None of that was here when this was measured. (All of it
  has been ported since: see the parity table. The comparison has not been
  measured again with it in.)
- **Expect parity, give or take a tenth, not a leap.** A port would be a
  rewrite of the engine for about the same frame rate.

## What is here

- `src/map.ts`: loads the Babylon project's map format (manifest, chunk
  files, shared KTX2 textures, pooled meshes as instanced batches) and
  streams it: full detail near the camera, the merged far version beyond,
  nothing past the view distance. Chunks left behind are unloaded (meshes,
  colliders, grass), and a chunk's set-up is spread over frames, 3 ms at a
  time. `src/perf.ts` times that work (`perf.recording = true`, then
  `perf.slowest()` and `perf.totals()` in the console).
- `src/materials.ts`: what the map's materials need beyond glTF (wind,
  tiled surfaces broken up, the ground's worn paths, water). A level can
  lay its own values over a material's (`materials` in `src/config.ts`).
- `src/grass.ts`: grass planted on the map's ground, in patches drawn only
  near the camera. A patch is one draw call; a tuft is 28 bytes that the
  vertex shader turns into blades. How much is planted goes with the
  quality setting (`?grass=` overrides it).
- `src/physics.ts`: the physics world (Rapier) and the map's colliders: each
  unique mesh's triangles, a box or an upright cylinder for each placing of
  a pooled one.
- `src/player.ts`: the fox, moved by Rapier's character controller, with its
  animations. Third person (a camera that follows and keeps clear of
  walls) and first person (V, or zoom all the way in); swimming wherever
  the map's water is deep enough to float in.
- `src/input.ts`: keyboard and gamepad as actions ("is `jump` down?"), with
  the bindings in one place.
- `src/menu.ts`: the title screen, pause and settings, in HTML.
  `src/settings.ts`: what the player has chosen (graphics quality, volume,
  look speed, inverted look), saved in the browser, and what each level of
  quality means. `src/audio.ts`: sound on three's `AudioListener` and
  `Audio`, with a few effects synthesised until there are sound files.
- `src/spaces.ts`: places and the doors between them. An interior is a
  second map loaded beside the level and entered behind a fade; the place
  left is hidden and held, never unloaded. Each place has its own light
  (indoors: less sky, no haze, a higher exposure, shorter shadows).
- `src/interact.ts`: what the player can use with E, and the zones that
  tell when the player walks in or out, both from the map's markers.
- `src/worldState.ts`: what the player has changed, as named values saved in
  the browser (`town/gate.lowered`); the player's whereabouts are kept there
  too, relative to the place they are in.
- `src/dialogue.ts`, `src/conversations.ts`, `src/stories/*.ink`: dialogue
  written in Ink (compiled by the plugin in `vite.config.ts`), which reads
  and writes the saved world with `fact` and `set_fact`.
- `src/effects.ts`: fire, sparks, smoke and firelight at the map's `fx`
  markers. Particles are worked out on the GPU from the time alone (nothing
  is simulated or kept per particle) and drawn in a pass of their own over
  the picture; lights are point lights batched so that their number can
  change freely. A fire near the camera costs about a millisecond. One-off
  bursts (dust on landing, a splash, sparks, an explosion with a flash of
  light) are the same particles born all at once, from a small pool of
  sprites; rain is a box of streaks wrapped around the camera.
- `src/navigation.ts`: where characters can walk (Recast/Detour), built in
  6 m tiles a few per frame from the map's collision geometry and rebuilt
  only where a chunk came or went; paths, and a crowd of agents that avoid
  each other. `src/npcs.ts`: three foxes that wander the square and now and
  then trot over to the player. **N** draws the walkable area.
- `src/gate.ts`: the portcullis and its lever. `src/hud.ts`: the words on
  screen. `src/config.ts`: the level and its interiors.
- `src/main.ts`: the renderer (sun, cascades, sky and baked sky light, fog,
  the effect chain), the game loop that ties the above together, and the
  measuring hooks.
- `public/maps/test_town/`: the town, as exported by the Babylon project's
  Blender tools.

URL switches: `?continue` and `?newgame` (the saved game); `?menu` (show the
title screen in development) and `?quality=low|medium|high|ultra`; `?detail=` and
`?view=` (how far full detail and anything at all reach, in metres: `?detail=60&view=150`
shows chunks coming and going); `?webgl` (WebGL 2 backend), `?post=0`, `?shadows=0`; `?gi=0`,
`?ao=0`, `?traa=0`, `?bloom=0`, `?sharpen=0` for one effect at a time;
`?scale=` (how much of the canvas is drawn before upscaling, default 1: none)
and `?ratio=` (the cap on device pixel ratio, default 1.25). The comparison
table was measured with `?gi=0&scale=1`.

To measure, in the console:

```js
bench.setPixels(1527, 1471);
await bench.measure();   // { cpu, total } in milliseconds, medians of 120 frames
```

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
clock. The game therefore uses Rapier directly (`src/physics.ts`).

## Traps worth knowing

**Moving vertices in a shader makes everything that moves vibrate, unless
the previous position is moved too.** three's motion vectors compare where
a vertex is now with where it was, and "where it was" is the untouched
mesh. A `positionNode` that only moves the current position (wind, here)
makes every swaying leaf appear to jump by its whole sway each frame;
temporal anti-aliasing then discards its history for those pixels and the
picture never settles, with camera and wind both still. `displaced()` in
`src/materials.ts` adds the same offset to `positionPrevious`. Found by
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
unless it advances the tick itself, as `measure()` in `src/main.ts` does.
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
main.ts) and that works, with one condition: the scene pass must go on
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

## Judged by fresh critics

The look has been judged by reviewers who were given only stills
and a bar, never the code or the reasoning behind it. Six views were
captured from this game and from the Babylon one at the same size, cropped
alike, shuffled into anonymous pairs, and each critic was asked which of
each pair is closer to a shipped game's screenshot and why, with numbers.
Every critic sorted the twelve images into the two renderers correctly.

| Round | Change before it | Pairs won here | Its top fault with this game |
|---|---|---|---|
| 1 | none | 4 of 6 | soft picture; sky white toward the sun |
| 2 | anisotropic filtering, sky eased | 2 of 6, all slight | still soft; sky pale and empty |
| 3 | sharpening after anti-aliasing, sky turned down then eased | 6 of 6 | soft (now minor); water smeared; lamps do not glow |
| 4 | contact occlusion, warm ground light, soft shadow edges | 5 of 6 | water smeared (the one view lost); no distance haze |
| 5 | ground and grass, first attempt | 4 of 6 | bare bank at the moat (lost); the hall view was a spoiled capture (lost) |
| 6 | ground and grass, second attempt | 6 of 6, five clearly | soft trees and water; pale empty sky; no distance haze |

Read the pair counts loosely: a different critic judged each round, and
rounds 1 and 2 disagree about nearly the same pictures. What is certain is
what was measured (above). One caveat: the Babylon stills may carry its
on-screen place names, which this game draws outside the canvas.

More critics judged this game's stills alone against a shipped game's
screenshots. All of them said no.

| When | Stills that passed | Top fault |
|---|---|---|
| before any change | the hall | shade with no form; sky white toward the sun |
| after round 4's changes | the hall | house fronts in shade too dark and flat; nothing near white |
| after easing the occlusion and raising exposure | the hall, the square | bare flat ground with sparse oversized grass |
| ground and grass, first attempt | the square | the same, with new numbers: tufts too few and too small |
| ground and grass, second attempt | the hall, the square | blades darker than their ground; town lawns bare |
| blades lit and coloured as their ground | the hall, the moat | town lawns: khaki ground a tenth covered |

The ground and grass were also judged blind against themselves: the same
five views before and after, shuffled.

| Attempt | Views where the new version was preferred | What decided it |
|---|---|---|
| first | 2 of 5, both slightly | tufts had become tiny and sparse, the lawn olive, the path edge stair-stepped |
| second | 5 of 5, four clearly | fine dense blades against "agave or paper cut-outs" |
| third (blades lit and coloured as their ground) | 5 of 5, four clearly | the same; and the path's broken edge |

The first attempt is worth the row: it followed the previous critic's
words (smaller, darker, sharper) and a blind critic preferred what was
there before. What it got right was kept, and the three things it got
wrong are in "Grass darker than the ground it grows on" above.

Shade is no longer among the faults, and the meadow and the moat bank now
read as grass. What stands between this and a yes, in the last critic's
order: the town's lawns, which are khaki ground a tenth covered in blades
(the map's paint is thin and dry there: content, in the Blender level, not
this renderer), and the bare band between a path and its meadow; glass,
lamps and cobbles with no shine or glow; a picture whose sunlit stone sits
at one pale value; flat cloth and props; water that reflects only the sky.
Where lawn meets cobbles or a wall the edge is still a ruler's line.

## Credits

Physics is [Rapier](https://rapier.rs) (Apache-2.0). Navigation is
[recast-navigation-js](https://github.com/isaac-mason/recast-navigation-js) (MIT). Dialogue is
[Ink](https://www.inklestudios.com/ink), played by `inkjs` (MIT). The fox is the Khronos
glTF sample "Fox": model by PixelMannen (CC0), rigging and animation by
@tomkranis (CC-BY 4.0), glTF conversion by @AsoboStudio and @scurest (CC-BY 4.0).
The town's textures are from [Poly Haven](https://polyhaven.com) (CC0). The water's ripple map
(`public/textures/waternormals.jpg`) is from the three.js examples (MIT).
