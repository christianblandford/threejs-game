import * as THREE from "three/webgpu";
import { GLTFLoader, type GLTFParser } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";

/**
 * Loads a map exported by the Babylon scaffold's Blender tools
 * (tools/blender/export_map.py there): a manifest, one .glb per chunk at
 * full detail and one merged "far" .glb, textures as shared KTX2 files, and
 * every mesh that is placed many times stored once in global.glb with a
 * placeholder at each placing.
 *
 * This is the prototype's version of the Babylon project's src/engine/map.ts,
 * without streaming: everything within `viewDistance` of one point is loaded
 * once, at full detail within `detailDistance` and as far versions beyond,
 * which is what that engine has loaded when the camera stands at that point.
 *
 * The files are right-handed glTF and three.js is right-handed, so nothing is
 * mirrored here. Positions in the manifest are in the Babylon game's
 * left-handed space: negate x to get three's.
 */

export interface MapManifest {
  chunkSize: number;
  min: [number, number, number];
  max: [number, number, number];
  global: { file: string } | null;
  markers: { name: string; position: [number, number, number]; yaw: number; props: Record<string, unknown> }[];
  chunks: { id: string; min: [number, number, number]; max: [number, number, number]; lods: { file: string; triangles: number }[] }[];
}

export interface LoadedMap {
  manifest: MapManifest;
  root: THREE.Group;
  stats: { detailChunks: number; farChunks: number; meshes: number; batches: number; placings: number; materials: number; triangles: number };
  /** A marker's position in three's space. */
  marker(name: string): THREE.Vector3 | null;
}

interface Extras {
  mesh_id?: string;
  library?: boolean | number;
  shadows?: boolean | number;
  collider_only?: boolean | number;
}

const isTrue = (value: unknown): boolean => value === true || value === 1;

export async function loadMap(
  renderer: THREE.WebGPURenderer,
  url: string,
  options: { around: THREE.Vector3; detailDistance: number; viewDistance: number },
): Promise<LoadedMap> {
  const manifest = (await (await fetch(url)).json()) as MapManifest;
  const baseUrl = url.slice(0, url.lastIndexOf("/") + 1);

  const ktx2 = new KTX2Loader().setTranscoderPath("/basis/").detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx2);
  // Every chunk file names the same materials and the same texture files. One
  // material per name for the whole map, so each texture is fetched, decoded
  // and uploaded once and not once per chunk.
  const materials = new Map<string, Promise<THREE.Material>>();
  loader.register((parser: GLTFParser) => ({
    name: "MAP_shared_materials",
    loadMaterial(index: number) {
      const name = (parser.json.materials?.[index] as { name?: string } | undefined)?.name;
      if (!name) return null;
      let shared = materials.get(name);
      if (!shared) {
        shared = parser.loadMaterial(index);
        materials.set(name, shared);
      }
      return shared;
    },
  }));

  const root = new THREE.Group();
  root.name = "map";
  const stats = { detailChunks: 0, farChunks: 0, meshes: 0, batches: 0, placings: 0, materials: 0, triangles: 0 };

  // Pooled meshes: the one real copy of each (from global.glb), and where it is placed.
  const library = new Map<string, THREE.Mesh[]>();
  const placings = new Map<string, { matrices: THREE.Matrix4[]; casts: boolean }>();

  /** Files a loaded scene into the map: real meshes stay, library copies and placeholders are taken out. */
  function take(scene: THREE.Group, detail: boolean): void {
    scene.updateMatrixWorld(true);
    const remove: THREE.Object3D[] = [];
    scene.traverse((object) => {
      const extras = object.userData as Extras;
      if (extras.mesh_id) {
        const parts = (object as THREE.Mesh).isMesh ? [object as THREE.Mesh] : (object.children.filter((child) => (child as THREE.Mesh).isMesh) as THREE.Mesh[]);
        if (isTrue(extras.library)) {
          if (!library.has(extras.mesh_id)) library.set(extras.mesh_id, parts);
        } else {
          let entry = placings.get(extras.mesh_id);
          if (!entry) placings.set(extras.mesh_id, (entry = { matrices: [], casts: false }));
          entry.matrices.push(object.matrixWorld.clone());
          if (detail && extras.shadows !== false && extras.shadows !== 0) entry.casts = true;
        }
        remove.push(object);
        return;
      }
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      // A primitive of a pooled object is handled with its parent, above.
      if ((mesh.parent?.userData as Extras | undefined)?.mesh_id) return;
      const own = { ...(mesh.parent?.userData as Extras), ...extras };
      if (isTrue(own.collider_only)) {
        remove.push(mesh);
        return;
      }
      mesh.castShadow = detail && own.shadows !== false && own.shadows !== 0;
      mesh.receiveShadow = true;
      mesh.matrixAutoUpdate = false; // nothing in a map moves
      stats.meshes++;
      stats.triangles += (mesh.geometry.index?.count ?? mesh.geometry.attributes.position!.count) / 3;
    });
    for (const object of remove) object.removeFromParent();
    scene.matrixAutoUpdate = false;
    root.add(scene);
  }

  const distanceTo = (chunk: MapManifest["chunks"][number]): number => {
    // Chunk bounds are in the game's space: x is negated in three's.
    const x = -options.around.x;
    const dx = Math.max(chunk.min[0] - x, 0, x - chunk.max[0]);
    const dz = Math.max(chunk.min[2] - options.around.z, 0, options.around.z - chunk.max[2]);
    return Math.hypot(dx, dz);
  };

  if (manifest.global) take((await loader.loadAsync(baseUrl + manifest.global.file)).scene, true);
  const wanted = manifest.chunks
    .map((chunk) => ({ chunk, distance: distanceTo(chunk) }))
    .filter(({ distance }) => distance <= options.viewDistance)
    .map(({ chunk, distance }) => ({ detail: distance <= options.detailDistance || !chunk.lods[1], chunk }));
  // A few at a time, as a browser would have it anyway.
  let next = 0;
  await Promise.all(
    Array.from({ length: 6 }, async () => {
      while (next < wanted.length) {
        const { chunk, detail } = wanted[next++]!;
        const file = detail ? chunk.lods[0]!.file : chunk.lods[1]!.file;
        take((await loader.loadAsync(baseUrl + file)).scene, detail);
        if (detail) stats.detailChunks++;
        else stats.farChunks++;
      }
    }),
  );

  // Every pooled mesh becomes one instanced mesh per material, holding all of its placings.
  for (const [id, { matrices, casts }] of placings) {
    const parts = library.get(id);
    if (!parts) continue;
    for (const part of parts) {
      const batch = new THREE.InstancedMesh(part.geometry, part.material, matrices.length);
      batch.name = `pool:${id}`;
      matrices.forEach((matrix, i) => batch.setMatrixAt(i, matrix));
      batch.instanceMatrix.needsUpdate = true;
      batch.computeBoundingSphere();
      batch.computeBoundingBox();
      batch.castShadow = casts;
      batch.receiveShadow = true;
      batch.matrixAutoUpdate = false;
      root.add(batch);
      stats.batches++;
      stats.triangles += ((part.geometry.index?.count ?? part.geometry.attributes.position!.count) / 3) * matrices.length;
    }
    stats.placings += matrices.length;
  }
  stats.materials = materials.size;
  root.updateMatrixWorld(true);
  ktx2.dispose();

  return {
    manifest,
    root,
    stats,
    marker(name) {
      const found = manifest.markers.find((marker) => marker.name === name);
      return found ? new THREE.Vector3(-found.position[0], found.position[1], found.position[2]) : null;
    },
  };
}
