import * as THREE from "three/webgpu";
import type { LevelInfo } from "../config";
import type { LoadedMap } from "../engine/map";
import type { Spaces } from "../engine/spaces";
import type { WorldState } from "../engine/worldState";
import type { Player } from "./player";

/**
 * Where the player is, kept in the saved world, and picking the game up
 * there again.
 *
 * Kept relative to the place's own zero: where an interior sits in the
 * scene depends on what was loaded before it.
 */
export interface Saving {
  /** Is there a game to continue? */
  exists(): boolean;
  /** Writes down where the player is now. */
  save(): void;
  /** Puts the player where the saved game left them. */
  resume(): Promise<void>;
}

export function createSaving(systems: { state: WorldState; level: LevelInfo; map: LoadedMap; spaces: Spaces; player: Player }): Saving {
  const { state, level, map, spaces, player } = systems;
  const whereabouts = state.scope("player");
  const exists = (): boolean => whereabouts.peek("space") !== undefined;
  return {
    exists,
    save() {
      if (spaces.travelling) return;
      const at = player.position.clone().sub(spaces.origin(spaces.current));
      whereabouts.set("space", spaces.current);
      whereabouts.set("x", Math.round(at.x * 100) / 100);
      whereabouts.set("y", Math.round(at.y * 100) / 100);
      whereabouts.set("z", Math.round(at.z * 100) / 100);
      whereabouts.set("yaw", Math.round(player.yaw * 100) / 100);
    },
    async resume() {
      if (!exists()) return;
      const at = { position: new THREE.Vector3(whereabouts.get("x", 0), whereabouts.get("y", 0), whereabouts.get("z", 0)), yaw: whereabouts.get("yaw", 0) };
      const space = whereabouts.get("space", level.id);
      if (space === level.id) {
        map.update(at.position);
        await map.settled();
        player.teleport(at.position, at.yaw);
      } else await spaces.travel(space, at).catch((error) => console.warn("Could not pick the saved game up where it was:", error));
    },
  };
}
