import RAPIER from "@dimforge/rapier3d-compat";
import * as THREE from "three/webgpu";

/**
 * The physics world (Rapier): what stops the player walking through walls.
 *
 * Everything in a map is static, so the map adds colliders and never moves
 * them: the triangles of each unique mesh, and a box or an upright cylinder
 * for each placing of a pooled one (a barrel, a tree). The player is a
 * capsule moved by Rapier's character controller (src/game/player.ts).
 */

export type Collider = RAPIER.Collider;

export interface Physics {
  readonly RAPIER: typeof RAPIER;
  readonly world: RAPIER.World;
  /** Advances the world by `dt` seconds. */
  step(dt: number): void;
  /** A static collider from triangles in the world's coordinates (see `worldTriangles`). */
  addTriangles(vertices: Float32Array, indices: Uint32Array): RAPIER.Collider;
  /** A static box: `half` is half its size along its own axes. */
  addBox(centre: THREE.Vector3, half: THREE.Vector3, rotation: THREE.Quaternion): RAPIER.Collider;
  /** A static upright cylinder standing on `foot`. */
  addCylinder(foot: THREE.Vector3, radius: number, height: number): RAPIER.Collider;
  /** Takes a collider out of the world (its chunk was unloaded). */
  remove(collider: RAPIER.Collider): void;
  /** How far along a ray the first thing is, or null. `direction` must be unit length. */
  raycast(origin: THREE.Vector3, direction: THREE.Vector3, reach: number, ignore?: RAPIER.Collider): number | null;
}

const point = new THREE.Vector3();
/** A mesh's triangles where it stands in the world: for its collider, and for working out where characters can walk. */
export function worldTriangles(mesh: THREE.Mesh): { vertices: Float32Array; indices: Uint32Array } | null {
  const position = mesh.geometry.attributes.position as THREE.BufferAttribute | undefined;
  if (!position) return null;
  const vertices = new Float32Array(position.count * 3);
  for (let i = 0; i < position.count; i++) {
    point.fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld);
    vertices[i * 3] = point.x;
    vertices[i * 3 + 1] = point.y;
    vertices[i * 3 + 2] = point.z;
  }
  const index = mesh.geometry.index;
  const indices = index ? Uint32Array.from(index.array as ArrayLike<number>) : Uint32Array.from({ length: position.count }, (_, i) => i);
  return indices.length < 3 ? null : { vertices, indices };
}

export async function createPhysics(): Promise<Physics> {
  await RAPIER.init();
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const ray = new RAPIER.Ray({ x: 0, y: 0, z: 0 }, { x: 0, y: -1, z: 0 });

  return {
    RAPIER,
    world,
    step(dt) {
      world.timestep = Math.min(dt, 1 / 30);
      world.step();
    },
    addTriangles(vertices, indices) {
      return world.createCollider(RAPIER.ColliderDesc.trimesh(vertices, indices));
    },
    addBox(centre, half, rotation) {
      return world.createCollider(
        RAPIER.ColliderDesc.cuboid(Math.max(half.x, 0.01), Math.max(half.y, 0.01), Math.max(half.z, 0.01))
          .setTranslation(centre.x, centre.y, centre.z)
          .setRotation({ x: rotation.x, y: rotation.y, z: rotation.z, w: rotation.w }),
      );
    },
    addCylinder(foot, radius, height) {
      return world.createCollider(RAPIER.ColliderDesc.cylinder(height / 2, radius).setTranslation(foot.x, foot.y + height / 2, foot.z));
    },
    remove(collider) {
      world.removeCollider(collider, false);
    },
    raycast(origin, direction, reach, ignore) {
      ray.origin = { x: origin.x, y: origin.y, z: origin.z };
      ray.dir = { x: direction.x, y: direction.y, z: direction.z };
      const hit = world.castRay(ray, reach, true, undefined, undefined, ignore);
      return hit ? hit.timeOfImpact : null;
    },
  };
}
