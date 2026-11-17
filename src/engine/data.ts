/**
 * Three small tools for state kept as plain data: reading a value by its
 * path, saying what differs between two states, and a short hash of one
 * (two runs that hash alike are, for every practical purpose, alike).
 */

/** The value at a dotted path (`"player.position.0"`), or the whole thing for an empty path. */
export function valueAt(data: unknown, path: string): unknown {
  let at = data;
  for (const key of path.split(".").filter(Boolean)) {
    if (at === null || typeof at !== "object") return undefined;
    at = (at as Record<string, unknown>)[key];
  }
  return at;
}

export interface Difference {
  path: string;
  was: unknown;
  is: unknown;
}

/** Every leaf that differs between two states, by path. */
export function differences(was: unknown, is: unknown, path = ""): Difference[] {
  if (Object.is(was, is)) return [];
  const both = was !== null && is !== null && typeof was === "object" && typeof is === "object";
  if (!both) return [{ path, was, is }];
  const found: Difference[] = [];
  const keys = new Set([...Object.keys(was as object), ...Object.keys(is as object)]);
  for (const key of keys) found.push(...differences((was as Record<string, unknown>)[key], (is as Record<string, unknown>)[key], path ? `${path}.${key}` : key));
  return found;
}

/** Eight hex digits from any plain data (FNV-1a over its JSON). Keys are hashed in the order they were written, so build the two things the same way. */
export function hashOf(data: unknown): string {
  const text = JSON.stringify(data);
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}
