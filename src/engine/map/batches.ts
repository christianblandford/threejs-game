import * as THREE from "three/webgpu";
import { SMALLEST_CASTER } from "./shadowProxy";

/** Where one pooled mesh is placed by one file, and whether any of those placings casts a shadow. */
export interface Placings {
  matrices: THREE.Matrix4[];
  casts: boolean;
}

/** One pooled mesh drawn at all its placings: an instanced mesh per material, with room for `capacity`. */
interface Batch {
  capacity: number;
  count: number;
  meshes: THREE.InstancedMesh[];
}

/**
 * The map's pooled meshes (a barrel, a tree: stored once, placed many
 * times), each drawn as one instanced mesh at every placing in view.
 *
 * A batch is made once and then rewritten in place: making an instanced
 * mesh costs three.js a fraction of a millisecond for every pass that draws
 * it (the picture, each shadow cascade), and making all hundred-odd again
 * whenever one chunk changed was a frame of 150 ms.
 */
export interface Batches {
  /** The one real copy of each pooled mesh, by its id: one mesh per material. */
  readonly library: Map<string, THREE.Mesh[]>;
  /** The pooled meshes whose placings have changed since their batches were last written. Add to it; `write` empties it. */
  readonly stale: Set<string>;
  /** Instanced meshes there are, and placings drawn. */
  readonly count: number;
  readonly placings: number;
  /** Rewrites the batches of every stale mesh from the placings `of(id)` yields. */
  write(of: (id: string) => Iterable<Placings | undefined>): void;
  /** Frees every batch and the one real copy of each mesh. */
  dispose(): void;
}

export function createBatches(root: THREE.Group): Batches {
  const library = new Map<string, THREE.Mesh[]>();
  const stale = new Set<string>();
  const batches = new Map<string, Batch>();
  let placings = 0;
  let count = 0;

  return {
    library,
    stale,
    get count() {
      return count;
    },
    get placings() {
      return placings;
    },
    write(of) {
      for (const id of stale) {
        const parts = library.get(id);
        if (!parts) continue;
        const matrices: THREE.Matrix4[] = [];
        let casts = false;
        for (const entry of of(id)) {
          if (!entry) continue;
          for (const matrix of entry.matrices) matrices.push(matrix);
          casts ||= entry.casts;
        }

        let batch = batches.get(id);
        if (matrices.length > (batch?.capacity ?? 0)) {
          // Outgrown: made again with room to spare, so that it is seldom outgrown twice.
          if (batch) {
            placings -= batch.count;
            for (const mesh of batch.meshes) {
              mesh.removeFromParent();
              mesh.dispose();
            }
          }
          const capacity = Math.max(8, 2 ** Math.ceil(Math.log2(matrices.length * 1.5)));
          batch = { capacity, count: 0, meshes: parts.map((part) => new THREE.InstancedMesh(part.geometry, part.material, capacity)) };
          for (const mesh of batch.meshes) {
            mesh.name = `pool:${id}`;
            mesh.receiveShadow = true;
            mesh.matrixAutoUpdate = false;
            root.add(mesh);
          }
          batches.set(id, batch);
        }
        if (!batch) continue;
        for (const mesh of batch.meshes) {
          matrices.forEach((matrix, i) => mesh.setMatrixAt(i, matrix));
          mesh.count = matrices.length;
          mesh.instanceMatrix.needsUpdate = true;
          mesh.visible = matrices.length > 0;
          if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
          // A prop too small to matter casts no shadow: a sign's bracket, a lamp's glass.
          mesh.castShadow = casts && mesh.geometry.boundingSphere!.radius >= SMALLEST_CASTER;
          mesh.boundingSphere = null; // worked out again from the placings it has now
          if (matrices.length > 0) mesh.computeBoundingSphere();
        }
        placings += matrices.length - batch.count;
        batch.count = matrices.length;
      }
      stale.clear();
      count = [...batches.values()].reduce((sum, batch) => sum + batch.meshes.length, 0);
    },
    dispose() {
      for (const batch of batches.values()) {
        for (const mesh of batch.meshes) {
          mesh.removeFromParent();
          mesh.dispose();
        }
      }
      for (const parts of library.values()) for (const part of parts) part.geometry.dispose();
      batches.clear();
      library.clear();
      stale.clear();
      placings = count = 0;
    },
  };
}
