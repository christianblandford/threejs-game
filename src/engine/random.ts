/**
 * Random numbers that can be had again: the same seed gives the same
 * numbers in the same order, and where it has got to can be read and put
 * back.
 *
 * Anything that decides what happens in the game (where a fox wanders,
 * when it changes its mind) draws from the engine's one of these, never
 * from `Math.random()`: a run is then its seed and the player's input, and
 * can be played again exactly. `Math.random()` is still right for what is
 * only heard or seen and never read back (the pitch of a footstep).
 */
export interface Random {
  readonly seed: number;
  /** Where the sequence has got to: save it, and set it to carry on from there. */
  state: number;
  /** The next number, from 0 up to but not including 1. */
  next(): number;
  /** A number from `from` up to but not including `to`. */
  between(from: number, to: number): number;
  /** Starts the sequence again, from this seed or its own. */
  reseed(seed?: number): void;
}

export function createRandom(seed = 1): Random {
  let state = seed >>> 0;
  const random: Random = {
    seed,
    get state() {
      return state;
    },
    set state(value: number) {
      state = value >>> 0;
    },
    // mulberry32: small, fast, and good enough for a game's dice.
    next() {
      state = (state + 0x6d2b79f5) >>> 0;
      let t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    },
    between: (from, to) => from + random.next() * (to - from),
    reseed(next) {
      if (next !== undefined) (random as { seed: number }).seed = next;
      state = random.seed >>> 0;
    },
  };
  return random;
}
