import { describe, expect, it } from "vitest";
import { createRandom } from "./random";
import { createSimulation, TICK, type Frame } from "./simulation";

const idle = (): Frame => ({ move: [0, 0], run: false, actions: [], look: [0, 0] });

function setup(blocked = { now: false }) {
  const seen: Frame[] = [];
  const simulation = createSimulation({ tick: (frame) => void seen.push(frame), idle, blocked: () => blocked.now, settle: async () => undefined });
  return { simulation, seen, blocked };
}

describe("the simulation's clock", () => {
  it("runs one tick for each sixtieth of a second that has passed", () => {
    const { simulation } = setup();
    expect(simulation.advance(TICK, idle)).toBe(1);
    expect(simulation.advance(TICK * 0.5, idle)).toBe(0);
    expect(simulation.advance(TICK * 0.5, idle)).toBe(1);
    expect(simulation.advance(TICK * 2, idle)).toBe(2);
    expect(simulation.tick).toBe(4);
  });

  it("says how far between two ticks the present is", () => {
    const { simulation } = setup();
    simulation.advance(TICK * 1.25, idle);
    expect(simulation.alpha).toBeCloseTo(0.25, 5);
  });

  it("does not sprint to catch up after a stall", () => {
    const { simulation } = setup();
    expect(simulation.advance(2, idle)).toBe(5);
    expect(simulation.advance(TICK, idle)).toBe(1);
  });

  it("stands still while held or blocked, and owes nothing for the time", () => {
    const { simulation, blocked } = setup();
    simulation.held = true;
    expect(simulation.advance(1, idle)).toBe(0);
    simulation.held = false;
    blocked.now = true;
    expect(simulation.advance(1, idle)).toBe(0);
    blocked.now = false;
    expect(simulation.advance(TICK, idle)).toBe(1);
  });

  it("uses input that was sent in place of the devices, and presses a press once", () => {
    const { simulation, seen } = setup();
    simulation.send({ move: [0, 1], actions: ["jump"] }, 3);
    expect(simulation.pending).toBe(3);
    simulation.advance(TICK * 4, () => ({ ...idle(), run: true }));
    expect(seen.map((frame) => frame.move[1])).toEqual([1, 1, 1, 0]);
    expect(seen.map((frame) => frame.actions)).toEqual([["jump"], [], [], []]);
    expect(seen[3]!.run).toBe(true);
  });

  it("steps by hand whatever the frame loop is doing, and records every tick", async () => {
    const { simulation } = setup();
    simulation.held = true;
    simulation.send({ move: [1, 0] }, 2);
    expect(await simulation.step(3)).toBe(3);
    expect(simulation.recorded.map((frame) => frame.move[0])).toEqual([1, 1, 0]);
  });

  it("starts again from nothing", async () => {
    const { simulation } = setup();
    await simulation.step(4);
    simulation.reset();
    expect(simulation.tick).toBe(0);
    expect(simulation.recorded).toHaveLength(0);
  });
});

describe("seeded random numbers", () => {
  it("gives the same numbers for the same seed, and others for another", () => {
    const a = createRandom(7);
    const b = createRandom(7);
    const c = createRandom(8);
    const first = [a.next(), a.next(), a.next()];
    expect([b.next(), b.next(), b.next()]).toEqual(first);
    expect([c.next(), c.next(), c.next()]).not.toEqual(first);
    expect(first.every((value) => value >= 0 && value < 1)).toBe(true);
  });

  it("carries on from a saved state", () => {
    const random = createRandom(3);
    random.next();
    const saved = random.state;
    const next = [random.next(), random.next()];
    random.state = saved;
    expect([random.next(), random.next()]).toEqual(next);
  });

  it("starts again when reseeded", () => {
    const random = createRandom(3);
    const first = random.next();
    random.reseed();
    expect(random.next()).toBe(first);
    random.reseed(4);
    expect(random.seed).toBe(4);
    expect(random.next()).not.toBe(first);
  });
});
