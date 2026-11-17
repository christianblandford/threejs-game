/**
 * The game's clock: the world moves in ticks, sixty a second, each given
 * one frame of input.
 *
 * Why not simply "time since the last frame": a world advanced by whatever
 * time happened to pass is a slightly different world on every machine and
 * every run. Advanced in fixed ticks, each from its input alone, a run is
 * its seed and its inputs. It can be stepped a tick at a time, recorded,
 * played again exactly, and driven by a test or an agent with nobody
 * watching, as fast or as slowly as it likes.
 *
 * The frame loop asks for the ticks that are due (`advance`) and draws
 * between the last two (`alpha`), so motion is as smooth on a display that
 * shows 120 frames a second as on one that shows 60. Something else can
 * hold the loop off and run ticks itself (`step`).
 */

/** Ticks a second, and the length of one (s). */
export const TICK_RATE = 60;
export const TICK = 1 / TICK_RATE;
/** A frame runs at most this many ticks: after a long stall the world does not sprint to catch up. */
const MOST_PER_FRAME = 5;

/** One tick's input: everything the world is told about what the player is doing. */
export interface Frame {
  /** Sideways and forward, each -1 to 1. */
  move: [number, number];
  run: boolean;
  /** What was pressed since the last tick: "jump", "interact", "toggleView", "up", "down", "answer1".."answer9". */
  actions: string[];
  /** Where the view is turned: heading and pitch (radians). */
  look: [number, number];
}

export interface Simulation {
  /** Ticks run since the start (or since `reset`). */
  readonly tick: number;
  /** How far the present moment is between the last two ticks, 0 to 1: what to draw between. */
  readonly alpha: number;
  /** While true the frame loop runs no ticks. `step` still does. */
  held: boolean;
  /** For the frame loop: runs the ticks that are due after `seconds` more, each with the input waiting to be used or else what `devices` says. Returns how many ran. */
  advance(seconds: number, devices: () => Frame): number;
  /** Runs `count` ticks now, waiting before each for the world to be ready. Resolves with the tick reached. */
  step(count?: number): Promise<number>;
  /** Uses this input for the next `ticks` ticks in place of the devices. What is left out is taken as "nothing pressed, looking where it looks". */
  send(frame: Partial<Frame>, ticks?: number): void;
  /** Lines up whole frames of input, one per tick, exactly as given: how a recording is played back. */
  queue(frames: readonly Frame[]): void;
  /** Ticks of sent input not yet used. */
  readonly pending: number;
  /** Every tick's input since the start: with the seed, the whole run. */
  readonly recorded: readonly Frame[];
  /** Back to tick 0 with nothing recorded or waiting. */
  reset(): void;
  /** The clock put back to an earlier tick (a snapshot was restored): what was recorded after it is dropped. */
  rewind(tick: number): void;
}

export interface SimulationHooks {
  /** One tick of the world. */
  tick(frame: Frame, index: number): void;
  /** Nothing pressed, looking where the view looks now. */
  idle(): Frame;
  /** True while the world must not tick: behind a menu, in the middle of going through a door. */
  blocked(): boolean;
  /** Resolves when the world is loaded and still enough to tick: `step` waits for it. */
  settle(): Promise<void>;
}

export function createSimulation(hooks: SimulationHooks): Simulation {
  let tick = 0;
  let owed = 0;
  let alpha = 1;
  const waiting: Frame[] = [];
  const recorded: Frame[] = [];

  function run(frame: Frame): void {
    recorded.push(frame);
    hooks.tick(frame, tick);
    tick++;
  }

  const simulation: Simulation = {
    held: false,
    get tick() {
      return tick;
    },
    get alpha() {
      return alpha;
    },
    get pending() {
      return waiting.length;
    },
    recorded,
    advance(seconds, devices) {
      if (simulation.held || hooks.blocked()) {
        // Time that passes while the world stands still is not owed to it afterwards.
        owed = 0;
        alpha = 1;
        return 0;
      }
      owed += seconds;
      let ran = 0;
      while (owed >= TICK && ran < MOST_PER_FRAME) {
        run(waiting.shift() ?? devices());
        owed -= TICK;
        ran++;
        if (hooks.blocked()) {
          owed = 0;
          break;
        }
      }
      if (ran === MOST_PER_FRAME) owed = 0;
      alpha = Math.min(1, owed / TICK);
      return ran;
    },
    async step(count = 1) {
      for (let i = 0; i < count; i++) {
        await hooks.settle();
        run(waiting.shift() ?? hooks.idle());
      }
      owed = 0;
      alpha = 1;
      return tick;
    },
    send(frame, ticks = 1) {
      const idle = hooks.idle();
      for (let i = 0; i < ticks; i++) {
        waiting.push({
          move: frame.move ? [frame.move[0], frame.move[1]] : [0, 0],
          run: frame.run ?? false,
          // What is pressed is pressed once, on the first of the ticks: held for sixty ticks, "jump" is still one jump.
          actions: i === 0 ? [...(frame.actions ?? [])] : [],
          look: frame.look ? [frame.look[0], frame.look[1]] : [waiting.at(-1)?.look[0] ?? idle.look[0], waiting.at(-1)?.look[1] ?? idle.look[1]],
        });
      }
    },
    rewind(to) {
      tick = to;
      owed = 0;
      alpha = 1;
      waiting.length = 0;
      recorded.length = Math.min(recorded.length, to);
    },
    queue(frames) {
      for (const frame of frames) waiting.push({ move: [frame.move[0], frame.move[1]], run: frame.run, actions: [...frame.actions], look: [frame.look[0], frame.look[1]] });
    },
    reset() {
      tick = 0;
      owed = 0;
      alpha = 1;
      waiting.length = 0;
      recorded.length = 0;
    },
  };
  return simulation;
}
