/**
 * Holds the frame rate by drawing fewer pixels.
 *
 * Most of a frame here is paid per pixel (bounced light, anti-aliasing,
 * shading), so when a view is too heavy for the machine the cheapest thing
 * to give up is resolution: the picture is built from several frames and
 * sharpened, and a tenth fewer pixels each way is hard to see. This watches
 * the time between frames and moves a scale between 1 and 0.6 in steps.
 *
 * Three things it has to be careful about, because all it can see is the
 * time between frames, and the browser only shows a frame on the display's
 * tick:
 *
 * - **It cannot see spare time.** A frame that takes 9 ms and one that
 *   takes 16 both arrive every 16.7. So it can only find out whether more
 *   pixels would fit by trying: after a quiet spell it steps up, and if
 *   frames are missed again it steps back and waits twice as long before
 *   the next try.
 * - **Slow is not always pixels.** A page held to 20 frames a second in a
 *   background window, a display that ticks 50 times a second, a frame
 *   that is slow in script: fewer pixels help none of them. Nor can one
 *   step down be judged by itself: a frame that took 23 ms and now takes
 *   20 still arrives every 33. So once it starts down it keeps going while
 *   frames are missed, and judges at the bottom: if the whole way down
 *   bought nothing, it goes back to where it began and leaves the size
 *   alone for a good while.
 * - **One long frame is not a slow view.** A chunk arriving or a shader
 *   compiling is a hitch, and changing the canvas's size is another. It
 *   acts on the share of frames that missed a tick over most of a second,
 *   never on one, and ignores the frames just after its own change.
 */

/** The scales it moves between, as a share of the quality setting's pixels each way. */
const STEPS = [1, 0.9, 0.8, 0.7, 0.6];
/** Frames looked at before deciding anything, and before each further step once it is on its way down. */
const WINDOW = 45;
const WINDOW_GOING_DOWN = 24;
/** What it aims for (ms): sixty frames a second. (A display that ticks slower is one of the cases where a step down buys nothing.) */
const TARGET = 1000 / 60;
/** A frame this much over the target missed a tick. */
const MISSED = 1.4;
/** Step down when more than this share of frames missed one; step up only when fewer than `QUIET` did. */
const SLOW = 0.1; // a tenth of frames missed is 54 a second with a visible judder
const QUIET = 0.03;
/** Frames ignored after a change of size (the change itself is a hitch, and the anti-aliasing starts over). */
const SETTLE = 20;
/** Seconds of quiet before trying more pixels; doubled each time the try fails, up to `LONGEST`. */
const PATIENCE = 6;
const LONGEST = 120;
/** Seconds it leaves the size alone after going all the way down for nothing; doubled each time, up to `LONGEST_GIVE_UP`. */
const GIVE_UP = 60;
const LONGEST_GIVE_UP = 600;
/** A frame this long (ms) is a stall (a hidden page, a breakpoint), not a measurement. */
const STALL = 250;

export interface ResolutionGovernor {
  /** The share of the quality setting's pixels to draw, each way: 1 down to 0.6. */
  readonly scale: number;
  /** Whether it may act. Off, the scale goes back to 1. */
  enabled: boolean;
  /** Tell it how long the last frame took to arrive (ms). True when the scale changed: fit the canvas again. */
  sample(interval: number): boolean;
  /** Start over at full size: a new level, a new quality setting. */
  reset(): void;
}

export function createResolutionGovernor(): ResolutionGovernor {
  let step = 0;
  let enabled = true;
  const recent: number[] = [];
  let settling = SETTLE * 3; // the first frames of a level are loading
  let clock = 0; // seconds of frames seen, stalls left out
  /** What it is in the middle of, until the frames after its last change have been looked at: on its way down (from which step, and how slow frames were there), or trying a step up. */
  let trying: { kind: "down"; from: number; before: number } | { kind: "up" } | null = null;
  let patience = PATIENCE;
  let nextTry = PATIENCE;
  let giveUp = GIVE_UP;
  let blockedUntil = 0;

  function change(to: number): boolean {
    if (to === step) return false;
    step = to;
    recent.length = 0;
    settling = SETTLE;
    return true;
  }

  return {
    get scale() {
      return STEPS[step]!;
    },
    get enabled() {
      return enabled;
    },
    set enabled(value: boolean) {
      enabled = value;
    },
    reset() {
      step = 0;
      recent.length = 0;
      settling = SETTLE * 3;
      trying = null;
      patience = PATIENCE;
      nextTry = clock + PATIENCE;
      giveUp = GIVE_UP;
      blockedUntil = 0;
    },
    sample(interval) {
      if (!enabled) return change(0);
      if (interval > STALL || interval <= 0) return false;
      clock += interval / 1000;
      if (settling > 0) {
        settling--;
        return false;
      }
      recent.push(interval);
      if (recent.length < (trying?.kind === "down" ? WINDOW_GOING_DOWN : WINDOW)) return false;
      const mean = recent.reduce((sum, value) => sum + value, 0) / recent.length;
      const missed = recent.filter((value) => value > TARGET * MISSED).length / recent.length;
      recent.length = 0;

      if (trying?.kind === "down") {
        const { from, before } = trying;
        if (missed > SLOW) {
          // Still missing frames: further down, if there is any further.
          if (step < STEPS.length - 1) return change(step + 1);
          trying = null;
          nextTry = clock + patience;
          // At the bottom and no faster than at the top: it is not the pixels. Back to where it began.
          if (mean > before * 0.93) {
            blockedUntil = clock + giveUp;
            giveUp = Math.min(LONGEST_GIVE_UP, giveUp * 2);
            return change(from);
          }
          return false;
        }
        trying = null;
        giveUp = GIVE_UP;
        nextTry = clock + patience;
        return false;
      }
      if (trying?.kind === "up") {
        trying = null;
        if (missed > SLOW) {
          // More pixels did not fit. Back, and wait longer before asking again.
          patience = Math.min(LONGEST, patience * 2);
          nextTry = clock + patience;
          return change(Math.min(STEPS.length - 1, step + 1));
        }
        patience = PATIENCE;
        nextTry = clock + patience;
        return false;
      }

      if (missed > SLOW) {
        nextTry = clock + patience;
        if (step < STEPS.length - 1 && clock >= blockedUntil) {
          trying = { kind: "down", from: step, before: mean };
          return change(step + 1);
        }
      } else if (missed <= QUIET && step > 0 && clock >= nextTry) {
        trying = { kind: "up" };
        return change(step - 1);
      }
      return false;
    },
  };
}
