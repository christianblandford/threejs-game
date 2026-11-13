# three.js game prototype

One question, answered with measurements: **would the Babylon game scaffold
([babylon-game-engine-starter](https://github.com/christianblandford/babylon-game-engine-starter))
run faster, at the same quality, on three.js?**

This draws that project's test town with three.js r187: the same exported
map files, the same textures, and the nearest equivalents of its "high"
quality preset. It is a measuring rig, not a game: you can fly around, and
that is all.

```bash
npm install
npm run dev        # http://localhost:5173
```

Click to look around, WASD to move, Shift to go faster.

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
  wind, water). None of that is here. Ported, it would add back several
  milliseconds.
- **Expect parity, give or take a tenth, not a leap.** A port would be a
  rewrite of the engine for about the same frame rate.

## What is here

- `src/map.ts`: loads the Babylon project's map format (manifest, chunk
  files, shared KTX2 textures, pooled meshes as instanced batches).
- `src/main.ts`: the renderer (one sun with four 2048 px cascades, sky and
  baked sky light, fog, the effect chain) and the measuring hooks.
- `public/maps/test_town/`: the town, as exported by the Babylon project's
  Blender tools.

URL switches: `?webgl` (WebGL 2 backend), `?post=0`, `?shadows=0`, and
`?ao=0`, `?traa=0`, `?bloom=0`, `?sharpen=0` for one effect at a time.

To measure, in the console:

```js
bench.setPixels(1527, 1471);
await bench.measure();   // { cpu, total } in milliseconds, medians of 120 frames
```

## One trap worth knowing

three redraws the scene once per tick of its own animation loop. Call
`render()` twice in one tick and the second call redraws only the final
full-screen quad. A benchmark loop of hand-made frames therefore measures
almost nothing (the first numbers here said the bare scene took 2.7 ms)
unless it advances the tick itself, as `measure()` in `src/main.ts` does.
Counting the GPU's draw calls per frame is what caught it.

## Credits

The town's textures are from [Poly Haven](https://polyhaven.com) (CC0).
