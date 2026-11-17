/**
 * What the player has done to the world, remembered: a flat list of named
 * values that outlives the page (it is saved in the browser as it changes).
 *
 *     state.set("town/gate.lowered", true);
 *     state.get("town/gate.lowered", false);   // false is the answer when nothing is stored
 *
 * Names are `<place>/<thing>.<property>`; `scope("town")` gives a view that
 * writes the place for you. Ported from the Babylon project's
 * src/engine/worldState.ts: nothing in it depends on a renderer.
 */

export type StateValue = string | number | boolean;

export interface WorldState {
  get<T extends StateValue>(key: string, fallback: T): T;
  /** The stored value whatever its type, or undefined. */
  peek(key: string): StateValue | undefined;
  /** Stores a value; `undefined` forgets it. */
  set(key: string, value: StateValue | undefined): void;
  scope(prefix: string): WorldState;
  /** True if anything is stored: there is a game to continue. */
  readonly any: boolean;
  /** Forgets every value whose name starts with `prefix`: a place, say, and all that was done in it. */
  forget(prefix: string): void;
  /** Forgets everything: a new game. */
  reset(): void;
  flush(): void;
  /** Everything stored, as plain data. */
  snapshot(): Record<string, StateValue>;
  /** Replaces everything stored with a snapshot. */
  restore(snapshot: Record<string, StateValue>): void;
}

const VERSION = 1;

export function createWorldState(slot = "save"): WorldState {
  const storageKey = `three-world:${slot}`;
  const values = new Map<string, StateValue>();
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) ?? "null") as { version: number; values: Record<string, StateValue> } | null;
    if (stored?.version === VERSION) for (const [key, value] of Object.entries(stored.values)) values.set(key, value);
  } catch {
    /* storage blocked or corrupt: start empty */
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  function flush(): void {
    clearTimeout(timer);
    timer = undefined;
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: VERSION, saved: new Date().toISOString(), values: Object.fromEntries(values) }));
    } catch {
      /* storage blocked: the change still holds for this session */
    }
  }
  window.addEventListener("pagehide", () => timer !== undefined && flush());

  function view(prefix: string): WorldState {
    return {
      get<T extends StateValue>(key: string, fallback: T): T {
        const value = values.get(prefix + key);
        return typeof value === typeof fallback ? (value as T) : fallback;
      },
      peek: (key) => values.get(prefix + key),
      set(key, value) {
        const full = prefix + key;
        if (values.get(full) === value) return;
        if (value === undefined) values.delete(full);
        else values.set(full, value);
        timer ??= setTimeout(flush, 400);
      },
      scope: (inner) => view(`${prefix}${inner}/`),
      get any() {
        return values.size > 0;
      },
      forget(inner) {
        for (const key of [...values.keys()]) if (key.startsWith(prefix + inner)) values.delete(key);
        timer ??= setTimeout(flush, 400);
      },
      reset() {
        values.clear();
        flush();
      },
      flush,
      snapshot: () => Object.fromEntries(values),
      restore(snapshot) {
        values.clear();
        for (const [key, value] of Object.entries(snapshot)) values.set(key, value);
        flush();
      },
    };
  }
  return view("");
}
