import * as THREE from "three/webgpu";
import { findLevel } from "./config";
import { createBench } from "./engine/bench";
import { createEngine } from "./engine/engine";
import { params } from "./engine/params";
import { perf } from "./engine/perf";
import { createBridge } from "./game/bridge";
import { createGame, type GameSnapshot } from "./game/scene";

/**
 * Where the page starts: the engine, a game in it, the frame loop, and for
 * whoever is developing, `window.game` (the dev bridge: step the world,
 * read its state, play a run again) and `window.bench` (every system, and
 * the measuring hooks).
 *
 * The engine is src/engine/: what any game here is built on. The game is
 * src/game/: this game. `README.md` says what is where.
 */
// (Not called "game": a page element's id is also a name on `window`, and `window.game` is the dev bridge.)
const canvas = document.querySelector<HTMLCanvasElement>("#view")!;
const statsBox = document.querySelector<HTMLElement>("#stats")!;

const engine = await createEngine(canvas);
const level = findLevel(params.get("level"));
let game = await createGame(engine, level);
// `?hold` starts with the clock held: nothing moves until something steps it (a test, an agent).
game.simulation.held = params.has("hold");
const { renderer, viewport, loop } = engine;

/** True while one game is being taken down and the next put together: nothing to update. */
let rebuilding = false;
/**
 * Puts the game together again in the running page: the engine, the
 * compiled picture and the loaded map all stay, so it takes a moment and
 * not a reload.
 */
function rebuild(make: typeof createGame, keepState = false): Promise<void> {
  // One at a time, in the order asked for: a file saved twice in a moment asks twice, and a game must not be taken down twice.
  const job = queue.then(async () => {
    rebuilding = true;
    try {
      const { map } = game;
      const held = game.simulation.held;
      const playing = game.menu.state !== "menu";
      const was: GameSnapshot | null = keepState ? game.snapshot() : null;
      game.dispose(true);
      game = await make(engine, level, { map, start: playing ? "play" : null });
      game.simulation.held = held;
      if (was) await game.restore(was);
      expose();
    } finally {
      rebuilding = false;
    }
  });
  queue = job.catch(() => undefined);
  return job;
}
let queue: Promise<void> = Promise.resolve();
/** A fresh run from tick 0. With a save slot of the run's own (`?slot=`), the saved world starts empty too. */
async function restart(seed?: number): Promise<void> {
  engine.random.reseed(seed);
  engine.navigation.reseed(engine.random.seed);
  if (params.has("slot")) engine.state.reset();
  await rebuild(createGame);
}

loop.start((seconds, interval) => {
  viewport.sample(interval);
  if (!rebuilding) game.update(seconds);
  engine.draw();
  if (loop.frames % 20 === 0) {
    const info = renderer.info.render as unknown as { drawCalls: number };
    const size = viewport.governor.scale < 1 ? ` · ${Math.round(viewport.governor.scale * 100)}% size` : "";
    statsBox.textContent = `${Math.round(1000 / loop.frameTime)} fps · ${loop.frameTime.toFixed(1)} ms · ${info.drawCalls} draws · three r${THREE.REVISION} ${engine.backend}${size}${game.fly.active ? " · flying" : ""}`;
  }
});

// The dev bridge. It always speaks of the game that is running now.
const bridge = createBridge(engine, () => game, restart);
const bench = createBench(engine, { takeCamera: () => (game.fly.active = true), advance: (seconds, eye) => game.advance(seconds, eye) });
/** Everything, for the console: `bench.player`, `bench.pace()`, `bench.look(...)`. Made again whenever the game is. */
function expose(): void {
  Object.assign(window, {
    game: bridge,
    bench: {
      ...engine,
      ...game,
      ...bench,
      perf,
      restart,
      governor: viewport.governor,
      frame: engine.draw,
      compose: engine.picture.compose,
      setPixels: viewport.pin,
      /** Puts the camera somewhere, looking a given way, with that part of the map loaded. */
      async look(x: number, y: number, z: number, yaw = 0, pitch = 0) {
        const eye = new THREE.Vector3(x, y, z);
        game.map.update(eye);
        await game.map.settled();
        game.fly.place(eye, yaw, pitch);
      },
    },
  });
}
expose();
console.info("map", game.map.stats);

// Saving a file of the game (anything under src/game/) puts the game together
// again from the new code, where it was: same place, same state of the world.
// A change to the engine, or to anything this file itself imports, reloads the page.
if (import.meta.hot) {
  import.meta.hot.accept("./game/scene", (next) => {
    if (!next) return;
    void rebuild(next.createGame as typeof createGame, true).catch((error) => {
      console.error("Could not put the game together again from the new code; reloading.", error);
      location.reload();
    });
  });
}
