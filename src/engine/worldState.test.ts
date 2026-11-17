import { beforeEach, describe, expect, it, vi } from "vitest";
import { createWorldState } from "./worldState";

/** The browser's storage, and the one event the saved world listens for, as far as this needs them. */
function fakeBrowser(): Map<string, string> {
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => void stored.set(key, value),
    removeItem: (key: string) => void stored.delete(key),
  });
  vi.stubGlobal("window", { addEventListener: () => undefined });
  return stored;
}

describe("the saved world", () => {
  beforeEach(() => void fakeBrowser());

  it("gives the fallback until something is set", () => {
    const state = createWorldState("test");
    expect(state.get("gate.lowered", false)).toBe(false);
    state.set("gate.lowered", true);
    expect(state.get("gate.lowered", false)).toBe(true);
  });

  it("gives the fallback when what is stored is of another kind", () => {
    const state = createWorldState("test");
    state.set("count", "three");
    expect(state.get("count", 0)).toBe(0);
  });

  it("forgets a value set to undefined", () => {
    const state = createWorldState("test");
    state.set("fire.out", true);
    state.set("fire.out", undefined);
    expect(state.peek("fire.out")).toBeUndefined();
  });

  it("keeps a scope's names under its own", () => {
    const state = createWorldState("test");
    const town = state.scope("town");
    town.scope("fire").set("camp.out", true);
    expect(state.get("town/fire/camp.out", false)).toBe(true);
    expect(town.get("fire/camp.out", false)).toBe(true);
  });

  it("forgets everything under a prefix and nothing else", () => {
    const state = createWorldState("test");
    state.set("town/gate.lowered", true);
    state.set("town/fire/camp.out", true);
    state.set("player/x", 4);
    state.forget("town/");
    expect(state.peek("town/gate.lowered")).toBeUndefined();
    expect(state.peek("town/fire/camp.out")).toBeUndefined();
    expect(state.get("player/x", 0)).toBe(4);
  });

  it("is still there after a reload, once flushed", () => {
    const first = createWorldState("test");
    first.set("town/gate.lowered", true);
    first.flush();
    expect(createWorldState("test").get("town/gate.lowered", false)).toBe(true);
    expect(createWorldState("another").get("town/gate.lowered", false)).toBe(false);
  });

  it("is empty after a reset", () => {
    const state = createWorldState("test");
    state.set("player/x", 4);
    state.reset();
    expect(state.peek("player/x")).toBeUndefined();
    expect(createWorldState("test").peek("player/x")).toBeUndefined();
  });
});
