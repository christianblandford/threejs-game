import type { LoadedMap } from "./map";
import type { Navigation, NavSource } from "./navigation";

/**
 * Keeps the navigation fed with everything solid: every loaded map's
 * ground and walls, and whatever the game has shut besides (a gate).
 *
 * Worked out again, only where it changed, whenever a chunk comes or goes
 * or a blocker is set.
 */
export interface Walkable {
  /** Counts a map's ground from now on, and whenever its detail changes. */
  follow(map: LoadedMap): void;
  /** Stops counting a map's ground: the map is going. */
  forget(map: LoadedMap): void;
  /** Replaces what the game itself has shut. */
  block(sources: NavSource[]): void;
}

export function createWalkable(navigation: Navigation): Walkable {
  const walked: LoadedMap[] = [];
  let blockers: NavSource[] = [];
  function rewalk(): void {
    const meshes = [...blockers];
    const obstacles = [];
    for (const each of walked) {
      const solid = each.collisionGeometry();
      for (const piece of solid.meshes) meshes.push(piece);
      for (const obstacle of solid.obstacles) obstacles.push(obstacle);
    }
    void navigation.rebuild({ meshes, obstacles });
  }
  return {
    follow(map) {
      if (walked.includes(map)) return; // a new game on a map that stayed loaded
      walked.push(map);
      map.onDetailChanged = rewalk;
      rewalk();
    },
    forget(map) {
      const at = walked.indexOf(map);
      if (at < 0) return;
      walked.splice(at, 1);
      map.onDetailChanged = null;
      rewalk();
    },
    block(sources) {
      blockers = sources;
      rewalk();
    },
  };
}
