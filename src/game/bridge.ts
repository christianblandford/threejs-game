import { differences, hashOf, valueAt } from "../engine/data";
import type { Engine } from "../engine/engine";
import { drawByHand, gpuDone } from "../engine/renderer";
import type { Frame } from "../engine/simulation";
import type { Game, GameSnapshot } from "./scene";

/**
 * The dev bridge: `window.game`, what a test, an agent or a person at the
 * console drives a running game through.
 *
 * A run is its seed and its inputs. So: hold the clock (`time.pause()`, or
 * open the page with `?hold`), say what the player does (`input.send`,
 * `input.press`), run ticks (`time.step`), and read what happened
 * (`state.get`, `state.hash`, `capture.screenshot`). The same seed and the
 * same inputs give the same state, every time: `input.recording()` is a
 * run, and `input.replay()` plays one again from the start.
 *
 * Open the page with `?slot=<name>` for a saved world of the run's own:
 * `time.restart()` then starts from an empty world and leaves the real
 * saved game alone.
 */
export interface Recording {
  seed: number;
  level: string;
  frames: Frame[];
}

export function createBridge(engine: Engine, current: () => Game, restart: (seed?: number) => Promise<void>) {
  const { renderer, viewport, canvas } = engine;
  const logs: { tick: number; level: "error" | "warn"; message: string }[] = [];
  const sounds: { tick: number; name: string }[] = [];
  const tick = (): number => current().simulation.tick;
  const note = (level: "error" | "warn", message: string): void => void logs.push({ tick: tick(), level, message: message.slice(0, 400) });
  window.addEventListener("error", (event) => note("error", event.message));
  window.addEventListener("unhandledrejection", (event) => note("error", String(event.reason)));
  // What is played is written down, so a test can ask whether the landing was heard.
  const play = engine.audio.play.bind(engine.audio);
  engine.audio.play = (name, options) => {
    sounds.push({ tick: tick(), name });
    play(name, options);
  };

  /** Draws the world as it stands, by hand: whatever the frame loop is doing (in a hidden page, nothing). */
  function draw(frames = 1): void {
    const game = current();
    if (!game.fly.active) game.player.present(0, 1);
    game.npcs.present(1);
    for (let i = 0; i < frames; i++) {
      game.advance(0, engine.camera.position);
      drawByHand(renderer, engine.draw);
    }
  }

  const time = {
    tick,
    seed: () => engine.random.seed,
    paused: () => current().simulation.held,
    /** Holds the clock: the world ticks only when stepped. (Being behind a menu holds it too.) */
    pause: () => void (current().simulation.held = true),
    resume: () => void (current().simulation.held = false),
    /** Runs `count` ticks now, waiting for the world to be loaded before each, and draws. Resolves with the tick reached. */
    async step(count = 1) {
      const reached = await current().simulation.step(count);
      draw();
      return reached;
    },
    /** Runs on to this tick. Going back needs `state.restore` or `time.restart`. */
    async runTo(to: number) {
      if (to < tick()) throw new Error(`Already at tick ${tick()}: restore a snapshot or restart to go back.`);
      return time.step(to - tick());
    },
    /** A fresh run from tick 0, with this seed (default: the same again). The level stays loaded. With `?slot=`, the saved world starts empty. */
    async restart(seed?: number) {
      await restart(seed);
      await current().ready();
      draw();
      return engine.random.seed;
    },
  };

  const input = {
    /** Uses this input for the next `ticks` ticks in place of the keyboard, mouse and gamepad. What is left out is "nothing pressed, looking where it looks". */
    send: (frame: Partial<Frame>, ticks = 1) => current().simulation.send(frame, ticks),
    /** Presses an action once ("jump", "interact", "toggleView", "up", "down", "answer1"..), then nothing for the rest of `ticks`. */
    press: (action: string, ticks = 1) => current().simulation.send({ actions: [action] }, ticks),
    /** Walks (or with `run`, runs): sideways and forward, each -1 to 1, relative to where the view looks. */
    move: (x: number, y: number, ticks = 1, options: { run?: boolean } = {}) => current().simulation.send({ move: [x, y], run: options.run ?? false }, ticks),
    /** Turns the view: heading and pitch in radians, from the next tick on. */
    look: (yaw: number, pitch = current().player.pitch) => current().simulation.send({ look: [yaw, pitch] }, 1),
    pending: () => current().simulation.pending,
    /** Every tick's input since the run started: with the seed, the run. */
    recording: (): Recording => ({ seed: engine.random.seed, level: current().level.id, frames: structuredClone([...current().simulation.recorded]) }),
    /** Plays a recording from the start: restarts with its seed, then runs its ticks. Resolves with where it ended. */
    async replay(recording: Recording) {
      await time.restart(recording.seed);
      const game = current();
      game.simulation.held = true;
      game.simulation.queue(recording.frames);
      await time.step(recording.frames.length);
      return { tick: tick(), hash: state.hash() };
    },
  };

  const state = {
    /** The state of play as plain data, or the part of it at a dotted path (`"player.position"`). */
    get: (path = "") => structuredClone(valueAt(current().state(), path)),
    /** Everything the next tick depends on; and putting it back. */
    snapshot: () => structuredClone(current().snapshot()),
    async restore(snapshot: GameSnapshot) {
      await current().restore(snapshot);
      draw();
    },
    /** Eight hex digits that two runs share only if they are in the same state. */
    hash: () => hashOf(current().snapshot()),
    /** What differs between two states or snapshots, by path. */
    diff: (was: unknown, is: unknown) => differences(was, is),
  };

  const capture = {
    /** A PNG data URL of the view, at this size if given, drawn for the purpose. */
    async screenshot(size: { width?: number; height?: number } = {}) {
      const sized = size.width !== undefined && size.height !== undefined;
      const wasPinned = viewport.pinned;
      const before = { width: canvas.width, height: canvas.height };
      if (sized) viewport.pin(size.width!, size.height!);
      try {
        // A few frames, for the picture to settle: it is built from several.
        draw(sized ? 12 : 4);
        const shot = canvas.toDataURL("image/png");
        await gpuDone(renderer);
        return shot;
      } finally {
        if (sized) {
          if (wasPinned) viewport.pin(before.width, before.height);
          else viewport.release();
        }
      }
    },
  };

  return {
    /** Resolves once the world around the player is loaded and still, with where things stand. */
    async ready() {
      await current().ready();
      return { tick: tick(), level: current().level.id, space: current().spaces.current };
    },
    time,
    input,
    state,
    capture,
    /** Errors and warnings since a tick. */
    logs: (sinceTick = 0) => logs.filter((entry) => entry.tick >= sinceTick),
    /** The sounds played since a tick. */
    sounds: (sinceTick = 0) => sounds.filter((entry) => entry.tick >= sinceTick),
  };
}

export type Bridge = ReturnType<typeof createBridge>;
