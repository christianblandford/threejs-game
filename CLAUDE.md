# three.js Game

The three.js counterpart of the Babylon game scaffold
(`babylon-game-engine-starter`): three r187 with the WebGPU renderer and node
materials, Vite, TypeScript. It draws the same exported maps and is being
brought to the same features. `README.md` covers what is here, the parity
table, the measurements and the traps; read it first.

## Working here

- `src/` is flat: one file per system, each a `createX()` that returns an
  interface. `src/main.ts` builds the renderer, creates the systems in order
  and runs the frame loop. There is no engine/game split yet.
- Import three's classes from `three/webgpu`, shader nodes from `three/tsl`,
  add-ons from `three/addons/...`. Not from plain `three`: that entry is
  built around the older WebGL renderer and has neither the WebGPU renderer
  nor the node materials everything here is made of.
- **Native first.** Use what three.js ships (core and `three/addons`), then
  the library everyone recommends (Rapier, recast-navigation-js, inkjs), and
  write our own only where neither exists. The README lists which is which.
  `three/addons/physics/RapierPhysics.js` is not used: it loads Rapier from
  a CDN and steps the world on its own timer, so it cannot be paused. Rapier
  is used directly (`src/physics.ts`), with its own character controller.
- Verify three's APIs against `node_modules/three/src` and
  `node_modules/three/examples/jsm` before using them. The typings are loose
  around shader nodes: where a chain of node arithmetic will not type, give
  the values the local `Node = any` alias (as `src/effects.ts` does) and
  check the shape by running it.
- Anything that moves on its own advances by the `dt` handed to its
  `update`. Behind a menu `dt` is 0, so the world stands still. (Leaf sway,
  grass and water ripples use the shader's own clock and do not pause.)
- Check changes with `npm run typecheck` and `npm run build`, then in the
  browser. `window.bench` exposes every system (`renderer`, `scene`,
  `camera`, `map`, `player`, `physics`, `spaces`, `state`, `interact`,
  `conversations`, `effects`, `navigation`, `npcs`, `menu`, `input`,
  `audio`) and the measuring hooks (`pace`, `tune`, `measure`, `run`,
  `flicker`, `setPixels`, `look`, `frame`, `perf`).
- Verify rendering changes on both backends: WebGPU is the default,
  `?webgl` forces WebGL 2.

## Maps and coordinates

- Maps are built and exported in the Babylon repository
  (`tools/blender/`, `npm run map:export` there) and copied into
  `public/maps/<level>/`. The Blender conventions (custom properties on
  materials and objects, Empties for markers) are that project's; the code
  that reads them here is `sort()` in `src/map.ts` and `upgradeMaterial()` in
  `src/materials.ts`. An interior's files go under its level's folder and it
  is listed in `src/config.ts`.
- The glTF files are right-handed, as three is: nothing is mirrored. The
  manifest's numbers (markers, chunk bounds, the path masks and the height
  image's mapping) are in the Babylon game's left-handed space: negate x,
  and a heading of `yaw` there is `pi - yaw` here (`markerPosition`,
  `markerYaw` in `src/spaces.ts`).
- Water is found by a mark on its material (`userData.water`), not by
  comparing materials: the loader gives a mesh a clone of its material when
  the mesh has vertex colours, and a missed water mesh gets a collider.

## The picture

- The frame is a `RenderPipeline` put together by `compose(quality)` in
  `src/main.ts`, and put together again when the quality setting changes.
  The scene pass must always write the same set of buffers (colour, diffuse
  colour, normal, velocity): asked for fewer than before, three.js throws
  while compiling every material and the screen goes black.
- A material that moves vertices in its `positionNode` must use
  `displaced()` (`src/materials.ts`), which moves the previous position too.
  Without it everything that sways shimmers under temporal anti-aliasing.
- Particles (`src/effects.ts`) are drawn in their own pass over the picture,
  never in the main pass: there they would stamp squares into the buffers
  the effects read. They hide behind walls by comparing with the scene's
  depth, read at `screenUV` explicitly; the pass's `getViewZNode()` samples
  with the sprite's own texture coordinates inside a sprite material.
- Shade gets its form from three things, each measured (README, "Shade
  with no form in it"): a short-reach occlusion multiplied into the bounced
  light's own before the one smoothing pass, a warm ground under the sky in
  the baked sky light, and soft shadow edges. An occlusion that is not
  smoothed flickers.
- A tiled surface (any material with `metres`) is broken up by `surface()`
  in `src/materials.ts`, the counterpart of the Babylon game's surface
  plugin: blotches, `detile`, and for the ground the worn paths, with their
  ragged edge. A level's own corrections to a material go in `materials` in
  `src/config.ts`, not in the shader.
- Grass (`src/grass.ts`) is patches of a tuft drawn many times, placed by
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
  anisotropy 8, the sky turned down and eased before it is drawn: the README's "Traps worth knowing" has the reasons
  and the numbers. Add to that section when something costs real time.

## Streaming and stutter

- Call `map.update(position)` every frame (`spaces.update` does it for every
  loaded map). A loaded file is set up by a generator a few milliseconds per
  frame (`setUp`, `pump` in `src/map.ts`); `await map.settled()` runs it to
  the end at once, for a loading screen or a door's fade.
- What makes streaming stutter is objects that are new to the renderer, not
  the set-up work: three.js prepares each object once per pass that draws
  it. Update what exists (the pooled props' instanced meshes are rewritten
  in place, `writeBatches`); do not dispose and remake. Reuse sprites and
  lights from a pool.
- What a setting costs: `bench.tune("gi", "0")` switches one of the
  `?name=value` switches in the running page, `await bench.pace()` times
  frames drawn back to back, `bench.tune("gi", null)` puts it back. Compare
  in one page, alternating: two loads differ by several milliseconds.
  The README's "Measured again" has the frame's breakdown.
- Solid map meshes do not cast shadows themselves: each chunk has one
  merged mesh on `SHADOW_LAYER` that only the shadow cameras see
  (`shadowProxy` in `src/map.ts`). Something added to a map that should
  cast and is not in a chunk's file sets its own `castShadow`. An effect
  node that is not a pass runs at the canvas's size unless it is wrapped
  in `rtt(...)` with a `resolutionScale`.
- The canvas's size is not fixed: `src/resolution.ts` lowers it when frames
  are missed (`bench.governor`; the frame counter shows "80% size").
  `bench.setPixels` and `bench.run` take the canvas out of its hands, so
  timings are not affected; a still or a timing taken any other way should
  check `bench.governor.scale` is 1 or open the page with `?autoscale=0`.
- Wrap occasional work in `timed("label", fn)` (`src/perf.ts`). To see
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

- Places and doors are `src/spaces.ts`, with the Babylon project's rules: an
  interior is a second map loaded 192 m past the level's edge and entered
  behind a fade; the place left is hidden (`map.shown`) and held
  (`map.hold`), never unloaded; an interior must be a closed box. Whatever
  is attached to "the map" must cope with several maps (`attach` in
  `src/main.ts` is where each new map is handed round).
- What should stay changed is written to the saved world
  (`src/worldState.ts`), named `<level>/<thing>.<property>`, and read back
  where the thing is built. Positions kept there are relative to
  `spaces.origin(place)`. "Restart level" forgets everything under the
  level's name; "New game" forgets everything. Both reload the page.
- Dialogue is Ink (`src/stories/*.ink`, compiled by the plugin in
  `vite.config.ts`). What a conversation needs from the game goes through
  facts in the saved world (`fact`, `set_fact`), not new functions.
- Things to use and walk-in zones come from map markers through
  `src/interact.ts`; what they do is decided by whoever listens for the name
  (mostly `src/main.ts`). Zones watch the player only.
- Navigation (`src/navigation.ts`) is fed `map.collisionGeometry()` from
  every loaded map and rebuilds only the tiles that changed. A piece whose
  shape changes needs a new source (a new id), not edited vertices.
  `src/npcs.ts` is the worked example of a character on it.
- Input is actions (`src/input.ts`): ask `input.isDown("jump")`, not for a
  key. Keyboard and gamepad feed the same actions; the mouse turns the view
  through three's `PointerLockControls`.
- The game starts on the title screen, except in development, where it
  starts playing (`?menu` shows the title screen). `?continue` and
  `?newgame` act on the saved game; `?quality=low|medium|high|ultra`
  overrides the saved setting.

## Testing in the app's browser pane

- A hidden or background pane throttles the page to about a frame a second
  and its screenshots go stale. Step the game by hand instead:
  `bench.player.update(1 / 30, { move: { x: 0, y: 1 }, run: false, jump:
  false }); bench.physics.step(1 / 30)` in a loop moves the player, and the
  hand-made frame above draws one.
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
  and a fixed answer form, never the reasoning. The README's "Judged by
  fresh critics" has the method and the rounds so far.
- A critic's count of pairs won moves a lot between critics. Trust what can
  be measured from the stills (share of the frame clipped, fine detail,
  saturation) and fix the one fault two critics agree on.
