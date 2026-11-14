/**
 * The places of the game: the level, and the interiors entered from it
 * through doors without leaving the scene (see src/spaces.ts). An interior's
 * files live under its level's folder.
 */

export interface InteriorInfo {
  id: string;
  name: string;
  map: string;
}

export const LEVEL = {
  /** Also the first part of every name this level keeps in the saved world ("town/gate.lowered"). */
  id: "town",
  name: "The town",
  /** On the title screen. */
  title: "Fox Town",
  map: "/maps/test_town/map.json",
  interiors: [{ id: "hall", name: "Town Hall", map: "/maps/test_town/interiors/hall/map.json" }] as InteriorInfo[],
  /**
   * Material properties laid over what the map's files say, by material
   * name. The map was lit for the Babylon game; this one's sun is brighter
   * on flat ground, where the pale earth of the paths washed out to one flat
   * tone (measured: a third of the Babylon picture's contrast on the road).
   * Held down here, it keeps its texture. (The grass was tried darker too
   * and a blind critic preferred it as it was: darker, it went olive.)
   */
  materials: { Dirt: { gain: 0.72 } } as Record<string, { gain?: number; variation?: number; detile?: number }>,
};
