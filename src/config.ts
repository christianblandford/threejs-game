import type { MaterialExtras } from "./engine/materials";

/**
 * The levels of the game, and for each the interiors entered from it
 * through doors without leaving the scene (see src/engine/spaces.ts).
 * `?level=<id>` starts in one. An interior's files live under its level's
 * folder.
 *
 * A new level is an entry here and a map exported into `public/maps/`.
 * What happens in it (who is met, what can be used) is the game's
 * (src/game/).
 */

export interface InteriorInfo {
  id: string;
  name: string;
  map: string;
}

export interface LevelInfo {
  /** Also the first part of every name this level keeps in the saved world ("town/gate.lowered"). */
  id: string;
  name: string;
  /** On the title screen. */
  title: string;
  map: string;
  /** Where the map is first loaded around, before its `spawn` marker has been read: [x, y, z]. */
  around: [number, number, number];
  interiors: InteriorInfo[];
  /** Zones that announce themselves when walked into: the zone's name in the map, and what to say. */
  places?: Record<string, string>;
  /** Material properties laid over what the map's files say, by material name. */
  materials?: Record<string, MaterialExtras>;
}

export const LEVELS: LevelInfo[] = [
  {
    id: "town",
    name: "The town",
    title: "Fox Town",
    map: "/maps/test_town/map.json",
    // The market square, where the Babylon measurements were taken.
    around: [0, 0.2, 18],
    interiors: [{ id: "hall", name: "Town Hall", map: "/maps/test_town/interiors/hall/map.json" }],
    places: { town_gate: "The town gate", market_square: "Market square", wall_walk: "The wall walk", hall_terrace: "The hall", moat: "The moat" },
    // The map was lit for the Babylon game; this one's sun is brighter on
    // flat ground, where the pale earth of the paths washed out to one flat
    // tone (measured: a third of the Babylon picture's contrast on the road).
    // Held down here, it keeps its texture. (The grass was tried darker too
    // and a blind critic preferred it as it was: darker, it went olive.)
    materials: { Dirt: { gain: 0.72 } },
  },
];

/** The level asked for by id, or the first when there is none of that name. */
export function findLevel(id: string | null): LevelInfo {
  return LEVELS.find((level) => level.id === id) ?? LEVELS[0]!;
}
