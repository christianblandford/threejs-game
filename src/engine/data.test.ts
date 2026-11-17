import { describe, expect, it } from "vitest";
import { differences, hashOf, valueAt } from "./data";

const state = { tick: 4, player: { position: [1, 2, 3], grounded: true }, world: { "town/gate.lowered": true } };

describe("state as plain data", () => {
  it("reads a value by its path", () => {
    expect(valueAt(state, "player.position.1")).toBe(2);
    expect(valueAt(state, "")).toBe(state);
    expect(valueAt(state, "player.nothing.here")).toBeUndefined();
  });

  it("says what differs between two states, and nothing when nothing does", () => {
    const later = structuredClone(state);
    later.player.position[2] = 5;
    later.player.grounded = false;
    expect(differences(state, later)).toEqual([
      { path: "player.position.2", was: 3, is: 5 },
      { path: "player.grounded", was: true, is: false },
    ]);
    expect(differences(state, structuredClone(state))).toEqual([]);
  });

  it("notices something added or taken away", () => {
    expect(differences({ a: 1 }, { a: 1, b: 2 })).toEqual([{ path: "b", was: undefined, is: 2 }]);
  });

  it("hashes the same data alike and different data differently", () => {
    const later = structuredClone(state);
    expect(hashOf(later)).toBe(hashOf(state));
    later.tick = 5;
    expect(hashOf(later)).not.toBe(hashOf(state));
    expect(hashOf(state)).toMatch(/^[0-9a-f]{8}$/);
  });
});
