# Measured against Babylon, and judged

This project began as a measurement: the same exported town drawn by
three.js r187 and by Babylon.js 9 (the
[babylon-game-engine-starter](https://github.com/christianblandford/babylon-game-engine-starter)
scaffold), on the same machine. three.js is the one being built on. This is
the record of that comparison, of how the frame was measured and brought
down afterwards, and of what critics who had never seen the project made of
its pictures. What building it taught is in [lessons.md](lessons.md).

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

### Measured again, and three things fixed

With everything since ported in, the square took 28 to 30 ms at 2.25
million pixels: about 34 frames a second in a full window. Each effect was
switched off in turn in one running page (`bench.tune`, timed with
`bench.pace`; two loads of the page differ by several milliseconds on their
own, so settings are compared back to back or not at all):

| | Cost |
|---|---|
| Smoothing the bounced light and the shade, two passes at full size | 8.5 ms |
| The bounced light itself, at half size | 6 ms |
| Shadows | 3 ms on the GPU, and 8 ms of script: 750 of 1,011 draw calls |
| Temporal anti-aliasing | 3 ms |
| Contact shade, bloom, sharpening | 1 ms, under 1, under 1 |
| The scene alone | 4 ms |

What was done, each checked against stills (a mean difference of 2 to 3
levels in 255, which is what two captures of the same thing differ by) and
the flicker measure (unchanged):

- **The smoothing runs at half size.** It was written into the picture's
  own shader, so it ran for every pixel of the canvas, twice, to clean
  something that was only ever worked out at half size. Drawn into a
  half-size texture first: 5.5 ms back.
- **The bounced light looks in one direction of eight steps at 0.4 of the
  canvas**, not two of six at half: 4.5 ms back.
- **A chunk casts its shadow as one mesh.** A chunk's buildings are five or
  six meshes, one per material, and each was drawn again into every shadow
  cascade. A shadow map holds only depth, so the solid ones are merged into
  one mesh per chunk that only the shadow cameras see (`src/engine/map/shadowProxy.ts`), and pooled props under half a metre cast nothing. Draw
  calls 1,011 to 411, script time 12 ms a frame to 5.5. (Counted just
  after loading. With the chunks around the starting point still loaded
  in detail the same view is 569 draw calls; the count before, in that
  state, was not taken.)

| At 2.25 million pixels | Before | After |
|---|---|---|
| The square | 28 to 30 ms | 16.5 to 17 ms |
| The terrace, looking over the town | not measured | 20 ms |
| The meadow by the road | not measured | 23.5 ms (grass is 2.5 of it) |
| The hall | not measured | 11.5 ms |

What is left is spread thin: bounced light 5 ms, anti-aliasing 3, shadows
3, the scene 4, trees and grass on top outdoors. None of it is waste of
the kind above. Past this point the lever is the number of pixels: the
"high" preset draws 1.25 device pixels per CSS pixel.

So the game now gives up pixels by itself when a view is too heavy
(`src/engine/resolution.ts`, "Lower resolution to hold 60 fps" in the settings,
on by default; `?autoscale=0` holds it off). It watches the share of
frames that miss the display's tick and moves the canvas between full
size and 0.6 of it each way, in steps of a tenth, never below 0.7 of a
CSS pixel. The frame counter shows the size when it is not full. Its
rules were worked out against made-up frame patterns (a heavy view, a
borderline one, a page held to 20 frames a second, a slow script, a hitch
every few seconds), and its hold on the canvas checked in the page. It
has not yet been watched through a real walk in a full-size window.

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
wrong are in "Grass darker than the ground it grows on" in [lessons.md](lessons.md).

Shade is no longer among the faults, and the meadow and the moat bank now
read as grass. What stands between this and a yes, in the last critic's
order: the town's lawns, which are khaki ground a tenth covered in blades
(the map's paint is thin and dry there: content, in the Blender level, not
this renderer), and the bare band between a path and its meadow; glass,
lamps and cobbles with no shine or glow; a picture whose sunlit stone sits
at one pale value; flat cloth and props; water that reflects only the sky.
Where lawn meets cobbles or a wall the edge is still a ruler's line.
