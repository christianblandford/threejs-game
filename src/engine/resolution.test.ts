import { describe, expect, it } from "vitest";
import { createResolutionGovernor } from "./resolution";

/** The display shows a frame on its next tick, sixty a second. */
const TICK = 1000 / 60;

/**
 * Plays `seconds` of frames through a governor. `cost` says how long a
 * frame takes to draw at a given scale (ms); it is shown on the tick after
 * that. Returns where the scale ended, how often it changed, and the share
 * of frames that missed a tick in the last ten seconds.
 */
function play(cost: (scale: number, at: number) => number | { exact: number }, seconds: number) {
  const governor = createResolutionGovernor();
  let time = 0;
  let changes = 0;
  const late: boolean[] = [];
  while (time < seconds * 1000) {
    const took = cost(governor.scale, time / 1000);
    const interval = typeof took === "number" ? Math.max(1, Math.ceil(took / TICK)) * TICK : took.exact;
    time += interval;
    if (time > (seconds - 10) * 1000) late.push(interval > TICK * 1.4);
    if (governor.sample(interval)) changes++;
  }
  return { scale: governor.scale, changes, missed: late.filter(Boolean).length / late.length };
}

describe("the resolution governor", () => {
  it("leaves a light view alone", () => {
    expect(play(() => 12, 60)).toMatchObject({ scale: 1, changes: 0 });
  });

  it("ignores a hitch every few seconds", () => {
    expect(play((_scale, at) => (Math.floor(at * 60) % 180 === 0 ? 90 : 12), 60)).toMatchObject({ scale: 1, changes: 0 });
  });

  it("steps a heavy view down until frames stop being missed", () => {
    // 23 ms at full size, 15.4 ms at 0.7: one step down still arrives every 33 ms, so it must keep going.
    const result = play((scale) => 8 + 15 * scale * scale, 120);
    expect(result.scale).toBe(0.7);
    expect(result.missed).toBeLessThan(0.1);
  });

  it("goes back up when the view gets lighter", () => {
    expect(play((scale, at) => (at < 40 ? 8 + 15 * scale * scale : 4 + 6 * scale * scale), 150).scale).toBe(1);
  });

  it("tries more pixels less and less often when they do not fit", () => {
    // Fits at 0.9 and not at 1: each try up is two changes, and the wait before the next doubles.
    const short = play((scale) => 6 + 11.5 * scale * scale, 60).changes;
    const long = play((scale) => 6 + 11.5 * scale * scale, 240).changes;
    expect(long - short).toBeLessThan(short);
  });

  it("gives the size back when fewer pixels buy nothing", () => {
    // A page held to 20 frames a second whatever it draws, and a frame that is slow in script.
    expect(play(() => ({ exact: 50 }), 30).scale).toBe(1);
    expect(play(() => 30, 30).scale).toBe(1);
  });

  it("does nothing when switched off, and returns to full size", () => {
    const governor = createResolutionGovernor();
    // Long enough to have started down, not so long that it has found fewer pixels buy nothing and come back.
    for (let i = 0; i < 150; i++) governor.sample(33.3);
    expect(governor.scale).toBeLessThan(1);
    governor.enabled = false;
    expect(governor.sample(33.3)).toBe(true);
    expect(governor.scale).toBe(1);
  });

  it("does not count a stall as a slow frame", () => {
    const governor = createResolutionGovernor();
    for (let i = 0; i < 400; i++) governor.sample(1000);
    expect(governor.scale).toBe(1);
  });
});
