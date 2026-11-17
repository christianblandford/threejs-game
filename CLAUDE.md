# three.js Game

A base for 3D browser games: three r187 with the WebGPU renderer and node
materials, Vite, TypeScript, and the Blender tools that build its levels.
`README.md` covers what it does and what is where; read it first. It grew
out of a Babylon.js scaffold (`babylon-game-engine-starter`) and shares its
map format; three.js is the one being built on.

## Working here

- `src/engine/` is what any game here is built on; `src/game/` is this
  game. Each system is one file (or a small folder) with a `createX()` that
  returns an interface. `createEngine` (`src/engine/engine.ts`) makes the
  engine's systems once and hands them round as one object;
  `createGame` (`src/game/scene.ts`) loads a level and puts the game
  together. `src/main.ts` only starts the two and the frame loop.
  Nothing in `engine/` imports from `game/`: keep it so.
- Keep files small and of one purpose. When one passes about 400 lines,
  look for the piece that stands by itself (types, a pure helper, one
  effect) and move it out, as `engine/map/` and `engine/navigation/` are
  split. A new system gets its own file, not a section in `scene.ts`.
- Levels are listed in `LEVELS` (`src/config.ts`); `?level=<id>` starts in
  one. What a level's named things do is `src/game/interactions.ts`.
- The page's `?name=value` switches are read through `src/engine/params.ts`
  (`numberOf`, `switchedOn`), beside their defaults.
- Tests are vitest, `*.test.ts` beside what they test (`npm test`). Pure
  logic gets a test; what needs a GPU is checked in the browser.
- Import three's classes from `three/webgpu`, shader nodes from `three/tsl`,
  add-ons from `three/addons/...`. Not from plain `three`: that entry is
  built around the older WebGL renderer and has neither the WebGPU renderer
  nor the node materials everything here is made of.
- **Native first.** Use what three.js ships (core and `three/addons`), then
  the library everyone recommends (Rapier, recast-navigation-js, inkjs), and
  write our own only where neither exists. `docs/lessons.md` lists which is which.
  `three/addons/physics/RapierPhysics.js` is not used: it loads Rapier from
  a CDN and steps the world on its own timer, so it cannot be paused. Rapier
  is used directly (`src/engine/physics.ts`), with its own character controller.
- Verify three's APIs against `node_modules/three/src` and
  `node_modules/three/examples/jsm` before using them. The typings are loose
  around shader nodes: where a chain of node arithmetic will not type, give
  the values the local `Node = any` alias (as `src/engine/effects.ts` does) and
  check the shape by running it.
- The world moves in ticks (`src/engine/simulation.ts`): sixty a second,
  each from one frame of input. What decides what happens goes in `tick`
  in `src/game/scene.ts`, takes `TICK` as its time, and never reads the
  clock or `Math.random()`: use `engine.random`. A run must stay its seed
  and its inputs. What is only seen (animation, the camera easing) is
  drawn per frame between the last two ticks (`present(seconds, alpha)`).
  Behind a menu no tick runs. (Leaf sway, grass and water ripples use the
  shader's own clock and do not pause.)
- Every part of the game can be taken down (`dispose`), and `scene.ts`
  takes them all down in `dispose()`: that is what lets a saved file be
  swapped into the running page, and a run be restarted in place. A new
  part of the game needs one too: listeners removed, objects out of the
  scene and the physics, anything it told the engine forgotten. Check it
  by saving the file twice with the game running: no reload, no second
  HUD, no error.
- State that the next tick depends on belongs in `snapshot()` and
  `restore()` of whatever owns it, and through them in the game's.
- Check changes with `npm run check` (type-check, tests, build: what CI runs), then in the
  browser. `window.bench` exposes every system (`renderer`, `scene`,
  `camera`, `map`, `player`, `physics`, `spaces`, `state`, `interact`,
  `conversations`, `effects`, `navigation`, `npcs`, `menu`, `input`,
  `audio`) and the measuring hooks (`pace`, `tune`, `measure`, `run`,
  `flicker`, `setPixels`, `look`, `frame`, `perf`).
- Verify rendering changes on both backends: WebGPU is the default,
  `?webgl` forces WebGL 2.

## Maps and coordinates

- Maps are built in Blender and exported here: `npm run map:export --
  maps/<name>.blend` writes `public/maps/<name>/`; `map:make-town` and
  `map:make-hall` build the test town and its hall by script
  (`tools/blender/`) and export them. `docs/maps.md` is the reference (the
  custom properties on objects, materials and Empties), and
  `docs/level-building.md` the guide. The code that reads the properties is
  `sort()` in `src/engine/map/index.ts` and `upgradeMaterial()` in
  `src/engine/materials.ts`: add a new one there, and to `docs/maps.md`.
  An interior's files go under its level's folder and it is listed in
  `src/config.ts`.
- `tools/blender/make_test_town.py` is the reference for building a dense
  level by script: one object per block of houses, linked duplicates for
  props, Empties for everything code refers to. Surface textures are Poly
  Haven sets in `maps/textures/<id>/` (`npm run texture:get -- <id>`,
  turned into materials by `surface()` in `tools/blender/surfaces.py`);
  trees and bushes come from `tools/blender/plants.py`. An export is
  repeatable: re-exporting an unchanged level changes no file in
  `public/maps/`, so a diff there is a real change to the level. Keep it
  so (`settle()` in `export_map.py`; check by exporting twice to two
  `--out` folders and comparing): the exported town is 57 MB, and an export
  that reshuffles it is 57 MB more in the repository's history each time.
- The glTF files are right-handed, as three is: nothing is mirrored. The
  manifest's numbers (markers, chunk bounds, the path masks and the height
  image's mapping) are in the Babylon game's left-handed space: negate x,
  and a heading of `yaw` there is `pi - yaw` here (`markerPosition`,
  `markerYaw` in `src/engine/spaces.ts`).
- Water is found by a mark on its material (`userData.water`), not by
  comparing materials: the loader gives a mesh a clone of its material when
  the mesh has vertex colours, and a missed water mesh gets a collider.

## The picture

- The frame is a `RenderPipeline` put together by `compose(quality)` in
  `src/engine/picture.ts`, and put together again when the quality setting changes.
  The scene pass must always write the same set of buffers (colour, diffuse
  colour, normal, velocity): asked for fewer than before, three.js throws
  while compiling every material and the screen goes black.
- A material that moves vertices in its `positionNode` must use
  `displaced()` (`src/engine/materials.ts`), which moves the previous position too.
  Without it everything that sways shimmers under temporal anti-aliasing.
- Particles (`src/engine/effects.ts`) are drawn in their own pass over the picture,
  never in the main pass: there they would stamp squares into the buffers
  the effects read. They hide behind walls by comparing with the scene's
  depth, read at `screenUV` explicitly; the pass's `getViewZNode()` samples
  with the sprite's own texture coordinates inside a sprite material.
- Shade gets its form from three things, each measured (`docs/lessons.md`, "Shade
  with no form in it"): a short-reach occlusion multiplied into the bounced
  light's own before the one smoothing pass, a warm ground under the sky in
  the baked sky light, and soft shadow edges. An occlusion that is not
  smoothed flickers.
- A tiled surface (any material with `metres`) is broken up by `surface()`
  in `src/engine/materials.ts`, the counterpart of the Babylon game's surface
  plugin: blotches, `detile`, and for the ground the worn paths, with their
  ragged edge. A level's own corrections to a material go in `materials` in
  `src/config.ts`, not in the shader.
- Grass (`src/engine/grass.ts`) is patches of a tuft drawn many times, placed by
  the vertex shader from 28 bytes a tuft; not an `InstancedMesh`. Three
  things there are load-bearing: the material's own `normalNode` (a
  two-sided material otherwise lights half the blades from below), the
  least width in pixels (thinner blades shimmer), and `uproot()` taking the
  shared tuft's buffers off a patch before disposing it. The blade colour
  is tuned against the ground texture: check both a meadow and a thin town
  lawn after changing it.
- Particles are unlit and the picture is HDR and tone-mapped: fire has to be
  several times brighter than sunlit stone, and smoke meant to show against
  lit ground has to be nearly opaque at its thickest.
- Point lights go through `DynamicLighting`, so their number can change
  without recompiling materials. Keep the one always-present dark point
  light in the scene, and give these lights no shadows.
- Sharpening after the temporal anti-aliasing only with `SharpenNode`'s
  `denoise` option (plain sharpening magnifies the bounced light's grain), a
  cap on what bloom and the bounced light are fed, map textures with
  anisotropy 8, the sky turned down and eased before it is drawn: `docs/lessons.md` ("Traps worth knowing") has the reasons
  and the numbers. Add to that section when something costs real time.

## Streaming and stutter

- Call `map.update(position)` every frame (`spaces.update` does it for every
  loaded map). A loaded file is set up by a generator a few milliseconds per
  frame (`setUp`, `pump` in `src/engine/map/index.ts`); `await map.settled()` runs it to
  the end at once, for a loading screen or a door's fade.
- What makes streaming stutter is objects that are new to the renderer, not
  the set-up work: three.js prepares each object once per pass that draws
  it. Update what exists (the pooled props' instanced meshes are rewritten
  in place: `src/engine/map/batches.ts`); do not dispose and remake. Reuse sprites and
  lights from a pool.
- What a setting costs: `bench.tune("gi", "0")` switches one of the
  `?name=value` switches in the running page, `await bench.pace()` times
  frames drawn back to back, `bench.tune("gi", null)` puts it back. Compare
  in one page, alternating: two loads differ by several milliseconds.
  `docs/measurements.md` ("Measured again") has the frame's breakdown.
- Solid map meshes do not cast shadows themselves: each chunk has one
  merged mesh on `SHADOW_LAYER` that only the shadow cameras see
  (`src/engine/map/shadowProxy.ts`). Something added to a map that should
  cast and is not in a chunk's file sets its own `castShadow`. An effect
  node that is not a pass runs at the canvas's size unless it is wrapped
  in `rtt(...)` with a `resolutionScale`.
- The canvas's size is not fixed: `src/engine/resolution.ts` lowers it when frames
  are missed (`bench.governor`; the frame counter shows "80% size").
  `bench.setPixels` and `bench.run` take the canvas out of its hands, so
  timings are not affected; a still or a timing taken any other way should
  check `bench.governor.scale` is 1 or open the page with `?autoscale=0`.
- Wrap occasional work in `timed("label", fn)` (`src/engine/perf.ts`). To see
  where a slow frame went: `await bench.run(points, speed)` flies a path and
  reports the worst frames split into streaming, drawing, GPU and arrivals.
- Compare timings only at a fixed canvas size and back to back
  (`bench.setPixels`, which `bench.run` does itself). The same flight
  measured an hour apart in a resized window differed by a factor of two.
- A hand-made frame must advance three's own tick, or only the last
  full-screen quad is redrawn: `renderer.info.reset();
  renderer._nodes.nodeFrame.update(); bench.frame()`, then wait for
  `renderer.backend.device.queue.onSubmittedWorkDone()` to include the GPU.

## The game's systems

- Places and doors are `src/engine/spaces.ts`, with the Babylon project's rules: an
  interior is a second map loaded 192 m past the level's edge and entered
  behind a fade; the place left is hidden (`map.shown`) and held
  (`map.hold`), never unloaded; an interior must be a closed box. Whatever
  is attached to "the map" must cope with several maps (`attach` in
  `src/game/scene.ts` is where each new map is handed round).
- What should stay changed is written to the saved world
  (`src/engine/worldState.ts`), named `<level>/<thing>.<property>`, and read back
  where the thing is built. Positions kept there are relative to
  `spaces.origin(place)`. "Restart level" forgets everything under the
  level's name; "New game" forgets everything. Both reload the page.
- Dialogue is Ink (`src/game/stories/*.ink`, compiled by the plugin in
  `vite.config.ts`). What a conversation needs from the game goes through
  facts in the saved world (`fact`, `set_fact`), not new functions.
- Things to use and walk-in zones come from map markers through
  `src/engine/interact.ts`; what they do is decided by whoever listens for the name
  (`src/game/interactions.ts`). Zones watch the player only.
- Navigation (`src/engine/navigation/index.ts`) is fed `map.collisionGeometry()` from
  every loaded map and rebuilds only the tiles that changed. A piece whose
  shape changes needs a new source (a new id), not edited vertices.
  `src/game/npcs.ts` is the worked example of a character on it.
- Input is actions (`src/engine/input.ts`): ask `input.isDown("jump")`, not for a
  key. Keyboard and gamepad feed the same actions; the mouse turns the view
  through three's `PointerLockControls`.
- The game starts on the title screen, except in development, where it
  starts playing (`?menu` shows the title screen). `?continue` and
  `?newgame` act on the saved game; `?quality=low|medium|high|ultra`
  overrides the saved setting.

## Testing in the app's browser pane

- Drive the game through `window.game` (`src/game/bridge.ts`), not by
  poking its parts: open the page with `?hold&slot=<name>`, then
  `await game.time.restart(seed)`, `game.input.move(0, 1, 60)`,
  `await game.time.step(60)`, `game.state.get()`,
  `await game.capture.screenshot()`. It works in a hidden or background
  pane, where the page's own frame loop runs about once a second, and it
  is exact: the same seed and inputs give the same `game.state.hash()`.
  `window.bench` is replaced whenever the game is rebuilt (a restart, a
  hot swap): read it again, do not keep it in a variable.
- A test that changes the world (`time.restart`, the gate) should run in
  its own save slot (`?slot=`): the default slot is the real saved game.
- If the page sits on "loading..." and `window.bench` never appears, check
  `await navigator.gpu.requestAdapter()`: when that hangs, the pane's GPU
  process has stopped answering. Open the page in a new tab.
- When the app's window is in the background the pane is held to 20 frames
  a second (`document.hasFocus()` is false, every frame reads 50 ms) and no
  timing means anything: `bench.measure`, `bench.run` and the frame
  counter all read the limit, not the game. Stills, flicker and the CPU
  timings in `bench.perf` are still good. Say that a cost was not measured
  rather than report such a number.
- A still taken straight after `spaces.travel` can be of a wall: wait a
  second for the camera to settle behind the player before capturing.
- The pane refuses pointer lock, so mouse look cannot be tried there. A
  gamepad can be simulated by replacing `navigator.getGamepads` with a
  function that returns a fake pad with `mapping: "standard"`.
- "New game", "Restart level" and several tests write to the browser's
  storage (`three-world:save`, `three-settings`): put back what a test
  changed.

## Judging the look

- Do not grade your own pictures. Capture stills at a fixed size
  (`bench.setPixels`, then `canvas.toDataURL` in the same frame as
  `bench.frame()`), crop this game's and the Babylon game's alike, shuffle
  them into anonymous pairs, and give them to a fresh reviewer with a bar
  and a fixed answer form, never the reasoning. `docs/measurements.md` ("Judged by
  fresh critics") has the method and the rounds so far.
- A critic's count of pairs won moves a lot between critics. Trust what can
  be measured from the stills (share of the frame clipped, fine detail,
  saturation) and fix the one fault two critics agree on.
