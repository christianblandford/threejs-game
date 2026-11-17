import * as THREE from "three/webgpu";
import { plantGrass, uproot, type GrassPatch } from "../grass";
import type { MaterialExtras } from "../materials";
import { disposeMaterial } from "../dispose";
import { timed } from "../perf";
import { navBox, navSource, type NavObstacle, type NavSource } from "../navigation";
import { worldTriangles, type Collider, type Physics } from "../physics";
import { readManifest, type MapManifest } from "./manifest";
import { createMapLoader } from "./sharedMaterials";
import { createBatches, type Placings } from "./batches";
import { SHADOW_LAYER, shadowProxy } from "./shadowProxy";
import { highestWater, waterFrom, type Water } from "./waterSurface";

export type { MapManifest };
export { SHADOW_LAYER };

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
  /** Takes the map out of the scene and frees everything it loaded: meshes, materials and textures, colliders, grass. */
  dispose(): void;
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
/** What `sort` found in a file. */
interface Sorted {
  /** What is drawn. */
  meshes: THREE.Mesh[];
  /** What casts a shadow through the chunk's one shadow-only mesh instead of by itself. */
  casters: THREE.Mesh[];
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
  const origin = options.origin?.clone() ?? new THREE.Vector3();
  const { manifest, baseUrl } = await readManifest(url, origin);
  const { loader, materials } = await createMapLoader(renderer, manifest, baseUrl, origin, options.materials);

  const root = new THREE.Group();
  root.name = "map";
  const stats = { detailChunks: 0, farChunks: 0, batches: 0, placings: 0, materials: 0, grass: 0, loading: 0, unloaded: 0 };
  const keepDistance = options.keepDistance ?? options.detailDistance + 48;

  // Pooled meshes: the one real copy of each (from global.glb), where the
  // always-loaded file places them, and one instanced mesh for each, drawn at
  // every placing that is in view at full detail.
  const batches = createBatches(root);
  const { library, stale } = batches;
  const globalPlacings = new Map<string, Placings>();

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
    const found: Sorted = { meshes: [], casters: [], solid: [], placed: [], grounds: [], waters: [] };
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
      // Solid things cast through the chunk's merged shadow mesh. What is cut out by its texture, or moved by the wind, still casts for itself.
      const material = mesh.material as THREE.Material & { positionNode?: unknown };
      if (mesh.castShadow && !Array.isArray(mesh.material) && !material.alphaTest && !material.transparent && !material.positionNode) {
        mesh.castShadow = false;
        found.casters.push(mesh);
      }
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
    if (found.casters.length > 0) {
      const merging = shadowProxy(scene, found.casters);
      for (;;) {
        const step = timed("chunk: shadow mesh", () => merging.next());
        if (step.done) {
          if (step.value) {
            scene.add(step.value);
            found.meshes.push(step.value); // freed with the chunk's other meshes
          }
          break;
        }
        yield;
      }
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

  /** Brings the batches of pooled meshes into line with what is shown (batches.ts): the always-loaded placings, and those of every chunk shown in detail. */
  function writeBatches(): void {
    batches.write(function* (id) {
      yield globalPlacings.get(id);
      for (const chunk of chunks) if (chunk.showing === "detail") yield chunk.placings.get(id);
    });
    stats.placings = batches.placings;
    stats.batches = batches.count;
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
  /** Set when the map is taken away: nothing more is loaded, and what arrives late is dropped. */
  let disposed = false;
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
    if (disposed) return;
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
          if (disposed) return; // arrived after the map had gone
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
    dispose() {
      disposed = true;
      jobs.length = 0;
      api.onDetailChanged = null;
      for (const chunk of chunks) {
        if (chunk.detail) unload(chunk, "detail");
        if (chunk.far) unload(chunk, "far");
      }
      for (const collider of always.colliders) physics?.remove(collider);
      root.traverse((object) => (object as THREE.Mesh).geometry?.dispose());
      batches.dispose();
      root.removeFromParent();
      // The materials were this map's own, one per name, with their textures.
      for (const shared of materials.values()) void shared.then(disposeMaterial).catch(() => undefined);
      materials.clear();
      for (const resolve of idle.splice(0)) resolve();
    },
    waterAt(x, z) {
      let highest = highestWater(always.water, x, z, null);
      for (const chunk of chunks) if (chunk.detail && chunk.water.length > 0) highest = highestWater(chunk.water, x, z, highest);
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
      if (disposed) return;
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
