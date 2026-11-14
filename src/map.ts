import * as THREE from "three/webgpu";
import { GLTFLoader, type GLTFParser } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { plantGrass, uproot, type GrassPatch } from "./grass";
import { upgradeMaterial, waterMaterial, type Blend, type MaterialExtras, type WaterDepth } from "./materials";
import { timed } from "./perf";
import { navBox, navSource, type NavObstacle, type NavSource } from "./navigation";
import { worldTriangles, type Collider, type Physics } from "./physics";

/**
 * Loads a map exported by the Babylon scaffold's Blender tools
 * (tools/blender/export_map.py there) and streams it around the camera: a
 * manifest, one .glb per chunk at full detail and one merged "far" .glb,
 * textures as shared KTX2 files, and every mesh that is placed many times
 * stored once in global.glb with a placeholder at each placing.
 *
 * Each chunk is shown at full detail within `detailDistance` of the camera
 * and as its far version beyond, out to `viewDistance`. A version is
 * loaded when it is first wanted and freed (meshes, colliders, grass) once
 * the camera has left it well behind: the detailed one past `keepDistance`,
 * everything past the view distance. What a newly loaded file needs before
 * it can be shown is done a few milliseconds per frame (`setUp`, `pump`).
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
  markers: { name: string; position: [number, number, number]; yaw: number; size?: [number, number, number]; shape?: "box" | "sphere"; props: Record<string, unknown> }[];
  blends?: { material: string; layer: string; mask: string; map: [number, number, number, number] }[];
  /** The terrain's height baked into an image, for water to know its depth. */
  heights?: { file: string; size: number; map: [number, number, number, number]; low: number; range: number } | null;
  chunks: { id: string; min: [number, number, number]; max: [number, number, number]; lods: { file: string; triangles: number }[] }[];
}

export interface LoadedMap {
  manifest: MapManifest;
  root: THREE.Group;
  readonly stats: { detailChunks: number; farChunks: number; batches: number; placings: number; materials: number; grass: number; loading: number; unloaded: number };
  /** A marker's position in three's space. */
  marker(name: string): THREE.Vector3 | null;
  /**
   * Call every frame with where the camera is. Loads and shows what is near
   * in detail and the rest from afar, frees what has been left far behind,
   * and does a few milliseconds of whatever set-up is waiting.
   */
  update(position: THREE.Vector3): void;
  /**
   * Streams around this point instead of wherever `update` says, until
   * called with null. For when the camera is in another map (an interior):
   * what is shown stays as the player left it.
   */
  hold(point: THREE.Vector3 | null): void;
  /** False hides everything of the map (its collision stays). For a map the player is not in. */
  shown: boolean;
  /**
   * Resolves once everything that `update` last asked for is loaded and
   * shown. Until then set-up is done at once and not a little per frame:
   * for a loading screen or a fade. With `hurry` false it keeps its
   * per-frame pace (something loaded ahead of need, while the game plays).
   */
  settled(hurry?: boolean): Promise<void>;
  /**
   * What stands in the world where full detail is loaded: every solid
   * mesh's triangles and every prop's simple shape. For working out where
   * characters can walk. Changes with `onDetailChanged`.
   */
  collisionGeometry(): { meshes: NavSource[]; obstacles: NavObstacle[] };
  /** Called when a chunk's detailed version has been set up or freed. */
  onDetailChanged: (() => void) | null;
  /**
   * The height of the water's surface over a point, or null where there is
   * none (where full detail is loaded). What makes water more than a
   * picture: the player floats at this height.
   */
  waterAt(x: number, z: number): number | null;
}

/** One mesh of water, flat and level, as triangles seen from above. */
interface Water {
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
function waterFrom(mesh: THREE.Mesh): Water | null {
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

/** What is solid in one loaded file. */
interface Solid {
  colliders: Collider[];
  nav: NavSource[];
  obstacles: NavObstacle[];
  grass: GrassPatch[];
  water: Water[];
}
const nothingSolid = (): Solid => ({ colliders: [], nav: [], obstacles: [], grass: [], water: [] });

interface Extras {
  mesh_id?: string;
  library?: boolean | number;
  shadows?: boolean | number;
  collider_only?: boolean | number;
  ground_cover?: string;
  /** What stops the player: the mesh's own triangles, a simple shape, or nothing. Default: `mesh`, or `box` for a pooled mesh. */
  collision?: "mesh" | "convex" | "box" | "sphere" | "cylinder" | "none";
  collision_radius?: number;
}
interface Placings {
  matrices: THREE.Matrix4[];
  casts: boolean;
}
/** One pooled mesh drawn at all its placings: an instanced mesh per material, with room for `capacity`. */
interface Batch {
  capacity: number;
  count: number;
  meshes: THREE.InstancedMesh[];
}
/** What `sort` found in a file. */
interface Sorted {
  /** What is drawn. */
  meshes: THREE.Mesh[];
  /** What needs a collider from its own triangles. */
  solid: THREE.Mesh[];
  /** Placings of pooled meshes, for their simple colliders. */
  placed: { id: string; matrix: THREE.Matrix4; extras: Extras }[];
  grounds: THREE.Mesh[];
  waters: THREE.Mesh[];
}
interface Chunk {
  info: MapManifest["chunks"][number];
  far: THREE.Group | null;
  detail: THREE.Group | null;
  farMeshes: THREE.Mesh[];
  detailMeshes: THREE.Mesh[];
  /** Props placed by the detailed version. */
  placings: Map<string, Placings>;
  colliders: Collider[];
  nav: NavSource[];
  obstacles: NavObstacle[];
  grass: GrassPatch[];
  water: Water[];
  loading: "none" | "far" | "detail";
  showing: "none" | "far" | "detail";
}
/** Milliseconds of set-up per frame while the game plays. */
const BUDGET = 3;

const isTrue = (value: unknown): boolean => value === true || value === 1;

export async function loadMap(
  renderer: THREE.WebGPURenderer,
  url: string,
  options: {
    around: THREE.Vector3;
    detailDistance: number;
    viewDistance: number;
    /** How much grass: 1 is the full sward, 0 none (see GRASS in grass.ts for how far it is drawn). */
    grass: number;
    /** Material properties by material name, laid over what the map's own files say (see `MaterialExtras`). */
    materials?: Record<string, MaterialExtras>;
    physics?: Physics;
    /**
     * A chunk's detailed version is freed once the camera is further than
     * this (m). Default: 48 m past `detailDistance`, so that turning back
     * after a short way costs nothing.
     */
    keepDistance?: number;
    /**
     * Where the map's own zero goes in the scene, for a second map beside the
     * first (an interior: see spaces.ts). Its author builds it around zero.
     */
    origin?: THREE.Vector3;
  },
): Promise<LoadedMap> {
  const manifest = (await (await fetch(url)).json()) as MapManifest;
  const baseUrl = url.slice(0, url.lastIndexOf("/") + 1);
  const origin = options.origin?.clone() ?? new THREE.Vector3();
  if (origin.lengthSq() > 0) {
    // Everything the manifest says in world coordinates moves with the map.
    // (It speaks the Babylon game's left-handed space: x there is -x here.)
    const shift = (point: [number, number, number]): void => {
      point[0] -= origin.x;
      point[1] += origin.y;
      point[2] += origin.z;
    };
    shift(manifest.min);
    shift(manifest.max);
    for (const marker of manifest.markers) shift(marker.position);
    for (const chunk of manifest.chunks) {
      shift(chunk.min);
      shift(chunk.max);
    }
  }
  const textures = new THREE.TextureLoader();

  // The worn-path masks, and the water's ripples.
  const blends = new Map<string, Blend>();
  for (const blend of manifest.blends ?? []) {
    const mask = await textures.loadAsync(baseUrl + blend.mask);
    mask.colorSpace = THREE.NoColorSpace;
    mask.wrapS = mask.wrapT = THREE.ClampToEdgeWrapping;
    blends.set(blend.material, { material: blend.material, layer: blend.layer, mask, map: blend.map });
  }
  const ripples = await textures.loadAsync("/textures/waternormals.jpg");
  // With the terrain's height image, water knows how deep it is and thins out toward the bank.
  let depth: WaterDepth | null = null;
  if (manifest.heights) {
    const heights = await textures.loadAsync(baseUrl + manifest.heights.file);
    heights.colorSpace = THREE.NoColorSpace;
    heights.minFilter = heights.magFilter = THREE.NearestFilter; // two bytes of one number: not to be blended
    heights.generateMipmaps = false;
    depth = { heights, ...manifest.heights, origin };
  }
  const water = waterMaterial(ripples, false, depth);
  const sea = waterMaterial(ripples, true, depth);
  // Marked, because the loader hands a mesh a copy of its material when the mesh has, say, vertex colours:
  // comparing with these two would then miss it, and the water would be given a collider to stand on.
  water.userData.water = sea.userData.water = true;

  const ktx2 = new KTX2Loader().setTranscoderPath("/basis/").detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx2);
  // Every chunk file names the same materials and the same texture files. One
  // material per name for the whole map, so each texture is fetched, decoded
  // and uploaded once and not once per chunk; and each is given what glTF
  // could not say about it (wind, worn paths, water) as it first arrives.
  const materials = new Map<string, Promise<THREE.Material>>();
  loader.register((parser: GLTFParser) => ({
    name: "MAP_shared_materials",
    loadMaterial(index: number) {
      const definition = parser.json.materials?.[index] as { name?: string; extras?: MaterialExtras } | undefined;
      const name = definition?.name;
      if (!name) return null;
      let shared = materials.get(name);
      if (!shared) {
        if (name === "Water" || name === "Sea") shared = Promise.resolve(name === "Sea" ? sea : water);
        else {
          const blend = blends.get(name) ?? null;
          shared = parser.loadMaterial(index).then(async (material) => {
            // A blended-in layer is another of the map's materials, sent ahead in the always-loaded file.
            const layer = blend ? ((await materials.get(blend.layer)) as THREE.MeshStandardMaterial | undefined) ?? null : null;
            return upgradeMaterial(material, { ...definition.extras, ...options.materials?.[name] }, blend, layer);
          });
        }
        materials.set(name, shared);
      }
      return shared;
    },
  }));

  const root = new THREE.Group();
  root.name = "map";
  const stats = { detailChunks: 0, farChunks: 0, batches: 0, placings: 0, materials: 0, grass: 0, loading: 0, unloaded: 0 };
  const keepDistance = options.keepDistance ?? options.detailDistance + 48;

  // Pooled meshes: the one real copy of each (from global.glb), where the
  // always-loaded file places them, and one instanced mesh for each, drawn at
  // every placing that is in view at full detail.
  const library = new Map<string, THREE.Mesh[]>();
  const globalPlacings = new Map<string, Placings>();
  const batches = new Map<string, Batch>();
  /** The pooled meshes whose placings have changed since their batches were last written. */
  const stale = new Set<string>();

  const physics = options.physics;
  const box = new THREE.Box3();
  const centre = new THREE.Vector3();
  const half = new THREE.Vector3();
  const foot = new THREE.Vector3();
  const turn = new THREE.Quaternion();
  const size = new THREE.Vector3();
  /** One placing of a pooled mesh as something solid: a box around it (turned and scaled with it), or an upright cylinder. */
  function collide(id: string, placed: THREE.Matrix4, extras: Extras, into: Solid): void {
    const kind = extras.collision ?? "box";
    const parts = library.get(id);
    if (kind === "none" || !parts) return;
    box.makeEmpty();
    for (const part of parts) {
      part.geometry.computeBoundingBox();
      box.union(part.geometry.boundingBox!);
    }
    placed.decompose(foot, turn, size);
    if (kind === "cylinder" || kind === "sphere") {
      // A tree's trunk: the radius its author gave, as tall as the mesh.
      const radius = Math.max((extras.collision_radius ?? Math.max(box.max.x - box.min.x, box.max.z - box.min.z) / 2) * (extras.collision_radius ? 1 : size.x), 0.05);
      const height = Math.max((box.max.y - box.min.y) * size.y, 0.2);
      if (physics) into.colliders.push(physics.addCylinder(foot, radius, height));
      into.obstacles.push({ position: foot.clone(), radius, height });
      return;
    }
    box.getCenter(centre).applyMatrix4(placed);
    box.getSize(half).multiply(size).multiplyScalar(0.5);
    if (physics) into.colliders.push(physics.addBox(centre, half, turn));
    into.nav.push(navBox(centre, half, turn));
  }

  /**
   * Sorts a loaded file: real meshes stay in it, library copies and
   * placeholders are taken out and noted. Nothing slow happens here: what
   * needs a collider or grass is listed, for `setUp` to do a piece at a time.
   */
  function sort(scene: THREE.Group, detail: boolean, placings: Map<string, Placings>): Sorted {
    scene.position.add(origin);
    scene.updateMatrixWorld(true);
    const remove: THREE.Object3D[] = [];
    const found: Sorted = { meshes: [], solid: [], placed: [], grounds: [], waters: [] };
    scene.traverse((object) => {
      const extras = object.userData as Extras;
      if (extras.mesh_id) {
        const parts = (object as THREE.Mesh).isMesh ? [object as THREE.Mesh] : (object.children.filter((child) => (child as THREE.Mesh).isMesh) as THREE.Mesh[]);
        if (isTrue(extras.library)) {
          if (!library.has(extras.mesh_id)) library.set(extras.mesh_id, parts);
        } else {
          let entry = placings.get(extras.mesh_id);
          if (!entry) placings.set(extras.mesh_id, (entry = { matrices: [], casts: false }));
          const matrix = object.matrixWorld.clone();
          entry.matrices.push(matrix);
          if (detail && extras.shadows !== false && extras.shadows !== 0) entry.casts = true;
          if (detail) found.placed.push({ id: extras.mesh_id, matrix, extras });
        }
        remove.push(object);
        return;
      }
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      // A primitive of a pooled object is handled with its parent, above.
      if ((mesh.parent?.userData as Extras | undefined)?.mesh_id) return;
      const own = { ...(mesh.parent?.userData as Extras), ...extras };
      const isWater = (mesh.material as THREE.Material).userData?.water === true;
      // What can be walked on or into: the mesh's own triangles. (Water is swum in, not stood on.)
      if (detail && !isWater && (own.collision ?? "mesh") !== "none") found.solid.push(mesh);
      if (isTrue(own.collider_only)) {
        remove.push(mesh);
        return;
      }
      mesh.castShadow = detail && !isWater && own.shadows !== false && own.shadows !== 0;
      mesh.receiveShadow = !isWater;
      mesh.matrixAutoUpdate = false; // nothing in a map moves
      found.meshes.push(mesh);
      if (detail && own.ground_cover) found.grounds.push(mesh);
      if (detail && isWater) found.waters.push(mesh);
    });
    for (const object of remove) object.removeFromParent();
    scene.matrixAutoUpdate = false;
    return found;
  }

  /**
   * Everything a loaded file needs before it can be shown, a piece at a
   * time: each `yield` is a place where the work can stop until the next
   * frame. One mesh's collider or one field's grass is a few milliseconds;
   * a whole chunk's at once is a frame dropped.
   */
  function* setUp(scene: THREE.Group, detail: boolean, placings: Map<string, Placings>, seed: number, into: Solid): Generator<void, THREE.Mesh[]> {
    const found = timed("chunk: sort", () => sort(scene, detail, placings));
    for (const mesh of found.waters) {
      const water = waterFrom(mesh);
      if (water) into.water.push(water);
    }
    yield;
    for (const mesh of found.solid) {
      timed("chunk: mesh collider", () => {
        const triangles = worldTriangles(mesh);
        if (!triangles) return;
        if (physics) into.colliders.push(physics.addTriangles(triangles.vertices, triangles.indices));
        into.nav.push(navSource(triangles.vertices, triangles.indices));
      });
      yield;
    }
    timed("chunk: prop colliders", () => {
      for (const prop of found.placed) collide(prop.id, prop.matrix, prop.extras, into);
    });
    yield;
    for (const [i, ground] of found.grounds.entries()) {
      // A field is tens of thousands of tufts: planted a slice at a time.
      const planting = plantGrass(ground, seed * 7 + i + 1, options.grass);
      for (;;) {
        const step = timed("chunk: grass", () => planting.next());
        if (step.done) {
          for (const patch of step.value) {
            scene.add(patch.mesh);
            into.grass.push(patch);
            stats.grass += patch.count;
          }
          break;
        }
        yield;
      }
      yield;
    }
    return found.meshes;
  }

  /**
   * Brings the batches of pooled meshes into line with what is shown. A
   * batch is made once and then rewritten in place: making an instanced mesh
   * costs three.js a fraction of a millisecond for every pass that draws it
   * (the picture, each shadow cascade), and making all hundred-odd again
   * whenever one chunk changed was a frame of 150 ms.
   */
  function writeBatches(): void {
    for (const id of stale) {
      const parts = library.get(id);
      if (!parts) continue;
      const matrices: THREE.Matrix4[] = [];
      let casts = false;
      const add = (entry: Placings | undefined): void => {
        if (!entry) return;
        for (const matrix of entry.matrices) matrices.push(matrix);
        casts ||= entry.casts;
      };
      add(globalPlacings.get(id));
      for (const chunk of chunks) if (chunk.showing === "detail") add(chunk.placings.get(id));

      let batch = batches.get(id);
      if (matrices.length > (batch?.capacity ?? 0)) {
        // Outgrown: made again with room to spare, so that it is seldom outgrown twice.
        if (batch) {
          stats.placings -= batch.count;
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
        mesh.castShadow = casts;
        mesh.boundingSphere = null; // worked out again from the placings it has now
        if (matrices.length > 0) mesh.computeBoundingSphere();
      }
      stats.placings += matrices.length - batch.count;
      batch.count = matrices.length;
    }
    stale.clear();
    stats.batches = [...batches.values()].reduce((sum, batch) => sum + batch.meshes.length, 0);
  }
  const markStale = (placings: Map<string, Placings>): void => {
    for (const id of placings.keys()) stale.add(id);
  };

  const chunks: Chunk[] = manifest.chunks.map((info) => ({
    info,
    far: null,
    detail: null,
    farMeshes: [],
    detailMeshes: [],
    placings: new Map(),
    colliders: [],
    nav: [],
    obstacles: [],
    grass: [],
    water: [],
    loading: "none",
    showing: "none",
  }));
  const eye = options.around.clone();
  let held: THREE.Vector3 | null = null;
  const distanceTo = (chunk: Chunk): number => {
    // Chunk bounds are in the game's space: x is negated in three's.
    const from = held ?? eye;
    const x = -from.x;
    const dx = Math.max(chunk.info.min[0] - x, 0, x - chunk.info.max[0]);
    const dz = Math.max(chunk.info.min[2] - from.z, 0, from.z - chunk.info.max[2]);
    return Math.hypot(dx, dz);
  };

  // --- Work spread over frames ---------------------------------------------------
  // Setting a loaded file up is a job: a generator that is run a little each
  // frame (`pump`). Whoever is waiting behind a loading screen or a fade
  // (`settled()`) gets them run to the end at once instead.
  const jobs: Generator<void, void>[] = [];
  let hurried = 0;
  /** Runs jobs for about `budget` milliseconds (always at least one piece); with no budget, to the end. */
  function pump(budget: number): void {
    const start = performance.now();
    while (jobs.length > 0) {
      if (jobs[0]!.next().done) jobs.shift();
      if (performance.now() - start >= budget) break;
    }
  }

  /** Frees one version of a chunk: its meshes, and with the detailed one its colliders, grass and prop placings. */
  function unload(chunk: Chunk, which: "far" | "detail"): void {
    timed("chunk: unload", () => {
      const scene = chunk[which]!;
      scene.removeFromParent();
      for (const mesh of which === "detail" ? chunk.detailMeshes : chunk.farMeshes) mesh.geometry.dispose();
      chunk[which] = null;
      if (which === "far") chunk.farMeshes = [];
      else {
        chunk.detailMeshes = [];
        for (const collider of chunk.colliders) physics?.remove(collider);
        chunk.colliders = [];
        chunk.nav = [];
        chunk.obstacles = [];
        chunk.water = [];
        detailChanged = true;
        for (const patch of chunk.grass) {
          stats.grass -= patch.count;
          uproot(patch);
        }
        chunk.grass = [];
        chunk.placings.clear();
      }
      stats.unloaded++;
    });
  }

  /**
   * Shows one version of a chunk in place of the other, all at once. Its
   * meshes are drawn for the first time in that frame, which costs three.js
   * some milliseconds for each; switching them on a few per frame instead
   * was tried and measured worse (three times the frames over 33 ms): what
   * a frame with anything new in it costs is mostly not per mesh.
   */
  function show(chunk: Chunk, want: "far" | "detail"): void {
    if (chunk.far) chunk.far.visible = want === "far";
    if (chunk.detail) chunk.detail.visible = want === "detail";
    if (chunk.showing === "detail" || want === "detail") markStale(chunk.placings);
    chunk.showing = want;
  }

  let active = 0;
  let idle: (() => void)[] = [];
  /** Something solid has come or gone since navigation was last told. */
  let detailChanged = false;
  /** Brings what is loaded and shown into line with where the camera is. */
  function refresh(): void {
    const queue: { chunk: Chunk; want: "far" | "detail"; distance: number }[] = [];
    for (const chunk of chunks) {
      const distance = distanceTo(chunk);
      const busy = chunk.loading !== "none";
      if (distance > options.viewDistance) {
        // Out of sight: hidden, and with a little slack (so the edge does not thrash) forgotten.
        if (busy || distance <= options.viewDistance + 16) continue;
        if (chunk.showing !== "none") {
          if (chunk.showing === "detail") markStale(chunk.placings);
          chunk.showing = "none";
        }
        if (chunk.detail) unload(chunk, "detail");
        if (chunk.far) unload(chunk, "far");
        continue;
      }
      // A little slack before giving detail up again, so a chunk at the edge does not flicker.
      const near = distance <= options.detailDistance || (chunk.showing === "detail" && distance <= options.detailDistance + 8);
      const want = near || !chunk.info.lods[1] ? "detail" : "far";
      // The detailed version is kept a while after it stops being shown (walking back is then free), and freed beyond that.
      if (chunk.detail && !busy && chunk.showing === "far" && distance > keepDistance) unload(chunk, "detail");
      const have = want === "detail" ? chunk.detail : chunk.far;
      if (!have) {
        if (chunk.loading === "none") queue.push({ chunk, want, distance });
        continue;
      }
      if (chunk.showing !== want) show(chunk, want);
      // Grass only close by: beyond that it is a few pixels and a great many triangles.
      // Patch by patch: the thick sward is only worth drawing a few steps from the camera.
      const grassy = chunk.showing === "detail";
      for (const patch of chunk.grass) {
        // To the nearest point of the patch: a tuft further off than `reach` has already sunk into the ground.
        const away = Math.hypot(Math.max(Math.abs(patch.x - eye.x) - patch.half, 0), Math.max(Math.abs(patch.z - eye.z) - patch.half, 0));
        // A little slack between appearing and disappearing, so a patch at the edge does not flicker.
        patch.mesh.visible = grassy && away < patch.reach + (patch.mesh.visible ? 3 : 0);
      }
    }
    queue.sort((a, b) => a.distance - b.distance);
    for (const { chunk, want } of queue) {
      if (active >= 4) break;
      active++;
      chunk.loading = want;
      const detail = want === "detail";
      const file = detail ? chunk.info.lods[0]!.file : chunk.info.lods[1]!.file;
      const done = (): void => {
        active--;
        chunk.loading = "none";
      };
      void loader
        .loadAsync(baseUrl + file)
        .then((gltf) => {
          const scene = gltf.scene;
          jobs.push(
            (function* (): Generator<void, void> {
              // Props are placed by the detailed version only; a far version has none.
              const meshes = yield* setUp(scene, detail, detail ? chunk.placings : new Map(), chunks.indexOf(chunk), detail ? chunk : nothingSolid());
              scene.visible = false;
              root.add(scene);
              chunk[want] = scene;
              if (detail) {
                chunk.detailMeshes = meshes;
                detailChanged = true;
              } else chunk.farMeshes = meshes;
              done();
            })(),
          );
          if (hurried > 0) settle();
        })
        .catch((error) => {
          console.error(`Chunk ${chunk.info.id} did not load:`, error);
          done();
          refresh();
        });
    }
    if (stale.size > 0) timed("write batches", writeBatches);
    if (detailChanged) {
      detailChanged = false;
      api.onDetailChanged?.();
    }
    stats.detailChunks = chunks.filter((chunk) => chunk.showing === "detail").length;
    stats.farChunks = chunks.filter((chunk) => chunk.showing === "far").length;
    stats.loading = active;
    stats.materials = materials.size;
    if (active === 0 && queue.length === 0 && jobs.length === 0) {
      for (const resolve of idle) resolve();
      idle = [];
      hurried = 0;
    }
  }
  /** Does everything that can be done now, without waiting for frames. */
  function settle(): void {
    for (;;) {
      refresh();
      if (jobs.length === 0) break;
      pump(Infinity);
    }
  }

  /** What is solid in the always-loaded file. */
  const always = nothingSolid();
  if (manifest.global) {
    const scene = (await loader.loadAsync(baseUrl + manifest.global.file)).scene;
    const job = setUp(scene, true, globalPlacings, 0, always);
    while (!job.next().done); // loading: nobody is watching frames go by
    markStale(globalPlacings);
    root.add(scene);
  }
  refresh();

  let tick = 0;
  const api: LoadedMap = {
    onDetailChanged: null,
    waterAt(x, z) {
      let highest: number | null = null;
      const look = (waters: Water[]): void => {
        for (const water of waters) {
          if (x < water.minX || x > water.maxX || z < water.minZ || z > water.maxZ) continue;
          if (highest !== null && water.level <= highest) continue;
          for (let i = 0; i < water.triangles.length; i += 6) {
            if (!insideTriangle(x, z, water.triangles, i)) continue;
            highest = water.level;
            break;
          }
        }
      };
      look(always.water);
      for (const chunk of chunks) if (chunk.detail && chunk.water.length > 0) look(chunk.water);
      return highest;
    },
    collisionGeometry() {
      const meshes = [...always.nav];
      const obstacles = [...always.obstacles];
      for (const chunk of chunks) {
        if (!chunk.detail) continue;
        for (const piece of chunk.nav) meshes.push(piece);
        for (const obstacle of chunk.obstacles) obstacles.push(obstacle);
      }
      return { meshes, obstacles };
    },
    manifest,
    root,
    stats,
    marker(name) {
      const found = manifest.markers.find((marker) => marker.name === name);
      return found ? new THREE.Vector3(-found.position[0], found.position[1], found.position[2]) : null;
    },
    update(position) {
      eye.copy(position);
      if (hurried > 0) {
        settle();
        return;
      }
      const before = jobs.length;
      if (before > 0) timed("chunk work this frame", () => pump(BUDGET));
      // Where the camera is matters a few times a second; work finished matters at once.
      if (tick++ % 12 === 0 || jobs.length < before) refresh();
    },
    hold(point) {
      held = point ? point.clone() : null;
      refresh();
    },
    get shown() {
      return root.visible;
    },
    set shown(on: boolean) {
      root.visible = on;
    },
    settled(hurry = true) {
      return new Promise((resolve) => {
        idle.push(resolve);
        if (hurry) {
          hurried++;
          settle();
        } else refresh();
      });
    },
  };
  return api;
}
