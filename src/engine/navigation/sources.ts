import * as THREE from "three/webgpu";

/**
 * What the walkable world is worked out from: pieces of solid geometry in
 * the world's own coordinates, each with an id that changes when its shape
 * does (so only the tiles it touches are built again).
 */

/** Triangles in the world's own coordinates: something walkable or in the way. */
export interface NavSource {
  /** Identifies the piece: a change of shape must come with a new id. */
  id: number;
  vertices: Float32Array;
  indices: Uint32Array;
  min: THREE.Vector3;
  max: THREE.Vector3;
}
/** An upright cylinder to walk around (a tree, a pillar). */
export interface NavObstacle {
  position: THREE.Vector3;
  radius: number;
  height: number;
}
export interface NavigationSources {
  meshes: NavSource[];
  obstacles?: NavObstacle[];
}

let nextSourceId = 1;
/** Wraps world-space triangles as a piece of the walkable world, working out its bounds. */
export function navSource(vertices: Float32Array, indices: Uint32Array): NavSource {
  const min = new THREE.Vector3(Infinity, Infinity, Infinity);
  const max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  for (let i = 0; i < vertices.length; i += 3) {
    const x = vertices[i]!, y = vertices[i + 1]!, z = vertices[i + 2]!;
    if (x < min.x) min.x = x;
    if (y < min.y) min.y = y;
    if (z < min.z) min.z = z;
    if (x > max.x) max.x = x;
    if (y > max.y) max.y = y;
    if (z > max.z) max.z = z;
  }
  return { id: nextSourceId++, vertices, indices, min, max };
}
/** A box, as a piece of the world that is in the way. */
export function navBox(centre: THREE.Vector3, half: THREE.Vector3, rotation: THREE.Quaternion): NavSource {
  const vertices = new Float32Array(24);
  const corner = new THREE.Vector3();
  for (let i = 0; i < 8; i++) {
    corner.set(i & 1 ? half.x : -half.x, i & 2 ? half.y : -half.y, i & 4 ? half.z : -half.z).applyQuaternion(rotation).add(centre);
    corner.toArray(vertices, i * 3);
  }
  // Its sides are walls to the navmesh whichever way they wind; its top is floor, out of an agent's reach unless it is lower than a step.
  const indices = new Uint32Array([0, 2, 1, 1, 2, 3, 4, 5, 6, 5, 7, 6, 0, 1, 4, 1, 5, 4, 2, 6, 3, 3, 6, 7, 0, 4, 2, 2, 4, 6, 1, 3, 5, 3, 7, 5]);
  return navSource(vertices, indices);
}
