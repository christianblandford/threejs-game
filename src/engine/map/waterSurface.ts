import type * as THREE from "three/webgpu";
import { worldTriangles } from "../physics";

/**
 * Where a map's water is, for whoever needs to know its height (the
 * player floats on it): each mesh of water as flat triangles seen from
 * above.
 */

/** One mesh of water, flat and level, as triangles seen from above. */
export interface Water {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  /** Six numbers (x, z of each corner) per triangle. */
  triangles: Float32Array;
  level: number;
}
function insideTriangle(x: number, z: number, t: Float32Array, i: number): boolean {
  const ax = t[i]!, az = t[i + 1]!, bx = t[i + 2]!, bz = t[i + 3]!, cx = t[i + 4]!, cz = t[i + 5]!;
  const d1 = (x - bx) * (az - bz) - (ax - bx) * (z - bz);
  const d2 = (x - cx) * (bz - cz) - (bx - cx) * (z - cz);
  const d3 = (x - ax) * (cz - az) - (cx - ax) * (z - az);
  return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
}
export function waterFrom(mesh: THREE.Mesh): Water | null {
  const world = worldTriangles(mesh);
  if (!world) return null;
  const { vertices, indices } = world;
  const water: Water = { minX: Infinity, maxX: -Infinity, minZ: Infinity, maxZ: -Infinity, triangles: new Float32Array(indices.length * 2), level: 0 };
  for (let i = 0; i < vertices.length; i += 3) {
    water.minX = Math.min(water.minX, vertices[i]!);
    water.maxX = Math.max(water.maxX, vertices[i]!);
    water.minZ = Math.min(water.minZ, vertices[i + 2]!);
    water.maxZ = Math.max(water.maxZ, vertices[i + 2]!);
    water.level += vertices[i + 1]! / (vertices.length / 3);
  }
  indices.forEach((index, i) => {
    water.triangles[i * 2] = vertices[index * 3]!;
    water.triangles[i * 2 + 1] = vertices[index * 3 + 2]!;
  });
  return water;
}

/** The highest water over a point among `waters`, or `above` (what was found elsewhere) if none of it is higher. */
export function highestWater(waters: Water[], x: number, z: number, above: number | null): number | null {
  let highest = above;
  for (const water of waters) {
    if (x < water.minX || x > water.maxX || z < water.minZ || z > water.maxZ) continue;
    if (highest !== null && water.level <= highest) continue;
    for (let i = 0; i < water.triangles.length; i += 6) {
      if (!insideTriangle(x, z, water.triangles, i)) continue;
      highest = water.level;
      break;
    }
  }
  return highest;
}
