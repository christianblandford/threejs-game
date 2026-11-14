import * as Recast from "@recast-navigation/core";
import * as RecastGenerators from "@recast-navigation/generators";
import * as THREE from "three/webgpu";
import { timed } from "./perf";

/**
 * AI navigation: works out where characters can walk, finds paths, and moves a
 * crowd of agents along them while they avoid each other.
 *
 * Recast/Detour (compiled to WebAssembly, `@recast-navigation`), ported from
 * the Babylon project's src/engine/navigation.ts with the same settings and
 * the same way of building: the navmesh is a grid of square tiles on a fixed
 * world-wide grid, built a few per frame and only where the geometry
 * actually changed. Building it whole would freeze the game for the best part
 * of a second every time the streamed map changes. Agents keep walking while
 * tiles are swapped underneath them.
 */

export interface NavigationOptions {
  /** How close an agent's centre may get to walls and drops, in metres. */
  agentRadius?: number;
  /** Agents fit under anything at least this far above the ground. */
  agentHeight?: number;
  /** Tallest ledge walked up without jumping. */
  maxStep?: number;
  /** Steepest walkable slope, in degrees. */
  maxSlope?: number;
  /** Horizontal resolution of the navmesh, in metres. Half the agent radius is the usual choice. */
  cellSize?: number;
  /** Side of one navmesh tile, in metres. A tile is built in one go, so smaller tiles keep each frame's share short. */
  tileSize?: number;
  /** Milliseconds per frame given to building tiles. The last tile started may run a millisecond or two over. */
  frameBudget?: number;
  /** Capacity of the crowd. */
  maxAgents?: number;
}

export const DEFAULT_NAVIGATION_OPTIONS: Readonly<Required<NavigationOptions>> = {
  agentRadius: 0.25,
  agentHeight: 0.6,
  maxStep: 0.18,
  maxSlope: 45,
  cellSize: 0.125,
  tileSize: 6,
  frameBudget: 1.5,
  maxAgents: 64,
};

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

export interface NavAgentOptions {
  /** Top speed in m/s. */
  speed?: number;
  /** How quickly it reaches that speed, in m/s². */
  acceleration?: number;
  radius?: number;
  height?: number;
}

/** A character driven along the navmesh. Position your model from `position` each frame. */
export interface NavAgent {
  /**
   * The agent's feet, on the navmesh. The navmesh floats a few centimetres
   * above the real ground: for a model to stand exactly on it, cast a short
   * ray down through this point, as npcs.ts does.
   */
  readonly position: THREE.Vector3;
  /** Metres per second. Zero when standing. */
  readonly velocity: THREE.Vector3;
  speed: number;
  /** Walks to the navmesh point nearest `target`. False if there is no navmesh near it. */
  goTo(target: THREE.Vector3): boolean;
  stop(): void;
  /** Moves instantly, dropping any destination. */
  teleport(position: THREE.Vector3): void;
  remove(): void;
}

export interface Navigation {
  /** True once the first build has finished. */
  readonly ready: boolean;
  /** Called each time the navmesh has caught up with the latest `rebuild`. */
  onRebuilt: (() => void) | null;
  /**
   * Makes the navmesh match `sources`. Only tiles whose geometry changed are
   * rebuilt, a few per frame and nearest the camera first, so call it
   * whenever the world changes; calls in quick succession are merged.
   * Resolves when the navmesh is up to date.
   */
  rebuild(sources: NavigationSources): Promise<void>;
  findPath(from: THREE.Vector3, to: THREE.Vector3): THREE.Vector3[];
  /** The navmesh point nearest `position`, or null if there is none within a couple of metres. */
  closestPoint(position: THREE.Vector3): THREE.Vector3 | null;
  /** A random point that can be walked to from `center`, roughly within `radius`. */
  randomPointAround(center: THREE.Vector3, radius: number): THREE.Vector3 | null;
  /** Adds an agent at the navmesh point nearest `start`. Works before the navmesh exists: the agent waits. */
  addAgent(start: THREE.Vector3, options?: NavAgentOptions): NavAgent;
  /** Draws the navmesh into `scene` (or stops). For debugging: redrawing it after each rebuild is slow. */
  showDebug(scene: THREE.Scene | null): void;
  /** Once a frame: builds a little, and moves the agents by `dt` seconds. `eye` decides what is built first. */
  update(dt: number, eye: THREE.Vector3): void;
  readonly stats: { tiles: number; queued: number };
}

/** Sides of the ring of walls that stands in for a cylinder obstacle. */
const OBSTACLE_SIDES = 8;
const OBSTACLE_RING = 1 / Math.cos(Math.PI / OBSTACLE_SIDES);
/** Rebuild requests wait this long (ms) for more to arrive: the map reports chunk changes in bursts. */
const REBUILD_SETTLE_TIME = 200;
/** No frame for this long (ms) means nothing is being drawn: still loading, or the tab is hidden. */
const NOT_RENDERING_AFTER = 250;
const OFFSCREEN_BUDGET = 16;
/** How far (m) from a position queries look for the navmesh: sideways, and up or down. */
const QUERY_REACH = { x: 2, y: 4, z: 2 };
/** Detour packs a tile number and a polygon number into 22 bits: 4096 tiles of up to 1024 polygons. */
const TILE_BITS = 12;
/** Voxels per `maxStep`. */
const STEP_CELLS = 6;
/** Detour crowd steering: anticipate turns, avoid and keep apart from other agents, keep straightening the path. */
const AGENT_UPDATE_FLAGS = 1 | 2 | 4 | 8 | 16;

interface Tile {
  x: number;
  y: number;
  fingerprint: number;
  minY: number;
  maxY: number;
  triangles: number[];
}
interface BuildJob {
  vertices: Recast.FloatArray;
  queue: Tile[];
}
interface Piece {
  min: THREE.Vector3;
  max: THREE.Vector3;
  hash: number;
  mesh?: NavSource;
  obstacle?: NavObstacle;
}

export async function createNavigation(options: NavigationOptions = {}): Promise<Navigation> {
  const settings = { ...DEFAULT_NAVIGATION_OPTIONS, ...options };
  await Recast.init();

  // Recast works on a voxel grid: `cs` is a cell's width, `ch` its height, and most other settings are counted in cells.
  const cs = settings.cellSize;
  const ch = settings.maxStep / STEP_CELLS;
  const cellsPerTile = Math.max(8, Math.round(settings.tileSize / cs));
  const tileSize = cellsPerTile * cs;
  const { config } = RecastGenerators.buildTiledNavMeshRcConfig({
    recastConfig: {
      ...Recast.recastConfigDefaults,
      cs,
      ch,
      tileSize: cellsPerTile,
      walkableSlopeAngle: settings.maxSlope,
      walkableHeight: Math.ceil(settings.agentHeight / ch),
      walkableClimb: STEP_CELLS,
      walkableRadius: Math.ceil(settings.agentRadius / cs),
      maxEdgeLen: 12 / cs,
      detailSampleMaxError: 3,
    },
    navMeshBounds: [
      [0, 0, 0],
      [tileSize, 1, tileSize],
    ], // unused: tiles are placed on a world-wide grid instead
  });
  const lidGap = ch * 3;
  /** A tile is shaped by geometry up to this far outside it, so that neighbouring tiles agree along their shared edge. */
  const tileMargin = config.borderSize * cs;
  const buildContext = new Recast.RecastBuildContext(false);

  // An empty navmesh whose tile grid is anchored at the world origin and goes
  // on in every direction, so tiles can come and go as the map streams.
  const navMesh = new Recast.NavMesh();
  navMesh.initTiled(
    Recast.NavMeshParams.create({
      orig: { x: 0, y: 0, z: 0 },
      tileWidth: tileSize,
      tileHeight: tileSize,
      maxTiles: 1 << TILE_BITS,
      maxPolys: 1 << (22 - TILE_BITS),
    }),
  );
  const query = new Recast.NavMeshQuery(navMesh);
  query.defaultQueryHalfExtents = QUERY_REACH;
  const crowd = new Recast.Crowd(navMesh, { maxAgents: settings.maxAgents, maxAgentRadius: settings.agentRadius * 2 });

  const built = new Map<number, Tile>();
  const tileKey = (x: number, y: number): number => (x + 0x8000) * 0x10000 + (y + 0x8000);
  const ringCount = (radius: number): number => Math.max(1, Math.ceil(radius / settings.agentRadius - 0.5));
  const agents = new Set<{ recover(): void; update(): void }>();
  const eye = new THREE.Vector3();
  let ready = false;
  let job: BuildJob | null = null;
  let requested: NavigationSources | null = null;
  let requestedAt = 0;
  const waiting: Array<() => void> = [];
  let warnedFull = false;
  let lastFrameAt = 0;
  let offscreenTimer: ReturnType<typeof setTimeout> | undefined;
  let frame = 0;
  let debugScene: THREE.Scene | null = null;
  let debugMesh: THREE.Mesh | null = null;

  // --- Building ----------------------------------------------------------------

  /** Works out which tiles are out of date and collects the triangles each of them needs. */
  function startJob(sources: NavigationSources): void {
    freeJob();
    const tiles = new Map<number, Tile>();
    const eachTile = (minX: number, minZ: number, maxX: number, maxZ: number, visit: (tile: Tile) => void): void => {
      const lastX = Math.floor((maxX + tileMargin) / tileSize);
      const lastY = Math.floor((maxZ + tileMargin) / tileSize);
      for (let x = Math.floor((minX - tileMargin) / tileSize); x <= lastX; x++) {
        for (let y = Math.floor((minZ - tileMargin) / tileSize); y <= lastY; y++) {
          const key = tileKey(x, y);
          let tile = tiles.get(key);
          if (!tile) tiles.set(key, (tile = { x, y, fingerprint: 0, minY: Infinity, maxY: -Infinity, triangles: [] }));
          visit(tile);
        }
      }
    };

    // 1. What is the world made of, and which tiles does each piece touch?
    const pieces: Piece[] = [];
    let vertexCount = 0;
    for (const mesh of sources.meshes) {
      if (mesh.indices.length === 0) continue;
      pieces.push({ min: mesh.min, max: mesh.max, hash: Math.imul(mesh.id + 1, 0x9e3779b1) ^ mesh.indices.length, mesh });
      vertexCount += mesh.vertices.length / 3;
    }
    for (const obstacle of sources.obstacles ?? []) {
      const { position, radius, height } = obstacle;
      const reach = radius * OBSTACLE_RING;
      const min = new THREE.Vector3(position.x - reach, position.y - radius, position.z - reach);
      const max = new THREE.Vector3(position.x + reach, position.y + height + lidGap, position.z + reach);
      const hash = Math.imul(position.x * 1000, 73856093) ^ Math.imul(position.z * 1000, 19349663) ^ Math.imul((radius + height) * 1000, 83492791);
      pieces.push({ min, max, hash, obstacle });
      vertexCount += OBSTACLE_SIDES * (2 * ringCount(reach) + 1);
    }
    for (const { min, max, hash } of pieces) {
      eachTile(min.x, min.z, max.x, max.z, (tile) => {
        tile.fingerprint = (tile.fingerprint + hash) | 0; // a sum, so the order pieces arrive in doesn't matter
        tile.minY = Math.min(tile.minY, min.y);
        tile.maxY = Math.max(tile.maxY, max.y);
      });
    }

    // 2. Tiles the world no longer reaches go at once; tiles whose surroundings changed are queued, nearest the camera first.
    for (const [key, tile] of built) {
      if (tiles.has(key)) continue;
      navMesh.removeTile(navMesh.getTileRefAt(tile.x, tile.y, 0));
      built.delete(key);
    }
    const stale = new Set<Tile>();
    for (const [key, tile] of tiles) if (built.get(key)?.fingerprint !== tile.fingerprint) stale.add(tile);
    if (stale.size === 0) return finishJob();
    const distance = (tile: Tile): number => Math.hypot((tile.x + 0.5) * tileSize - eye.x, (tile.y + 0.5) * tileSize - eye.z);
    const queue = [...stale].sort((a, b) => distance(b) - distance(a));

    // 3. Hand each stale tile the triangles in and around it.
    const positions = new Float32Array(vertexCount * 3);
    let v = 0;
    let a = 0;
    let b = 0;
    let c = 0;
    const addTriangle = (tile: Tile): void => {
      if (stale.has(tile)) tile.triangles.push(a, b, c);
    };
    for (const { min, max, mesh, obstacle } of pieces) {
      let needed = false;
      eachTile(min.x, min.z, max.x, max.z, (tile) => (needed ||= stale.has(tile)));
      if (!needed) continue;

      if (mesh) {
        // glTF winds a floor counter-clockwise seen from above, which is what Recast takes for walkable.
        positions.set(mesh.vertices, v * 3);
        const { indices } = mesh;
        for (let t = 0; t < indices.length; t += 3) {
          a = indices[t]! + v;
          b = indices[t + 1]! + v;
          c = indices[t + 2]! + v;
          const ax = positions[a * 3]!, az = positions[a * 3 + 2]!;
          const bx = positions[b * 3]!, bz = positions[b * 3 + 2]!;
          const cx = positions[c * 3]!, cz = positions[c * 3 + 2]!;
          eachTile(Math.min(ax, bx, cx), Math.min(az, bz, cz), Math.max(ax, bx, cx), Math.max(az, bz, cz), addTriangle);
        }
        v += mesh.vertices.length / 3;
      } else if (obstacle) {
        // Rings of walls with a lid: walls are never walkable, and the ground they stand on is blocked with them.
        const { position, radius } = obstacle;
        const reach = radius * OBSTACLE_RING;
        const corner = (ring: number, side: number, y: number): void => {
          const angle = (side / OBSTACLE_SIDES) * Math.PI * 2;
          const far = reach - ring * settings.agentRadius;
          positions.set([position.x + Math.cos(angle) * far, y, position.z + Math.sin(angle) * far], v++ * 3);
        };
        const triangles: number[] = [];
        for (let ring = 0; ring < ringCount(reach); ring++) {
          for (let side = 0; side < OBSTACLE_SIDES; side++) {
            const here = v;
            const next = side + 1 < OBSTACLE_SIDES ? v + 2 : v + 2 - OBSTACLE_SIDES * 2;
            triangles.push(here, next, here + 1, next, next + 1, here + 1);
            corner(ring, side, min.y);
            corner(ring, side, max.y - lidGap);
          }
        }
        for (let side = 0; side < OBSTACLE_SIDES; side++) {
          if (side >= 2) triangles.push(v - side, v - 1, v);
          corner(0, side, max.y);
        }
        eachTile(min.x, min.z, max.x, max.z, (tile) => {
          if (stale.has(tile)) for (const index of triangles) tile.triangles.push(index);
        });
      }
    }

    const vertices = new Recast.VerticesArray();
    vertices.copy(positions.subarray(0, v * 3));
    job = { vertices, queue };
  }

  function buildTile(current: BuildJob, tile: Tile): void {
    let data: Recast.UnsignedCharArray | undefined;
    if (tile.triangles.length > 0) {
      const triangles = new Recast.TrianglesArray();
      triangles.copy(tile.triangles);
      const index = new Recast.RecastChunkyTriMesh();
      index.init(current.vertices, triangles, tile.triangles.length / 3, 256);
      const result = RecastGenerators.generateTileNavMeshData(
        current.vertices,
        triangles,
        config,
        index,
        { x: tile.x, y: tile.y, bmin: [tile.x * tileSize, tile.minY, tile.y * tileSize], bmax: [(tile.x + 1) * tileSize, tile.maxY, (tile.y + 1) * tileSize] },
        {},
        false,
        buildContext,
      );
      if (result.success) data = result.data; // failure just means nothing here is walkable
      const { heightfield, compactHeightfield, contourSet, polyMesh, polyMeshDetail } = result.intermediates;
      if (heightfield) Recast.freeHeightfield(heightfield);
      if (compactHeightfield) Recast.freeCompactHeightfield(compactHeightfield);
      if (contourSet) Recast.freeContourSet(contourSet);
      if (polyMesh) Recast.freePolyMesh(polyMesh);
      if (polyMeshDetail) Recast.freePolyMeshDetail(polyMeshDetail);
      buildContext.resetLog();
      Recast.Raw.destroy(index.raw);
      triangles.destroy();
      tile.triangles = [];
    }
    // Swap the tile in place. The crowd moves agents standing on the old one onto the new one.
    navMesh.removeTile(navMesh.getTileRefAt(tile.x, tile.y, 0));
    if (data) {
      const { status } = navMesh.addTile(data, Recast.Detour.DT_TILE_FREE_DATA, 0);
      if (Recast.statusFailed(status)) {
        data.destroy();
        if (!warnedFull) console.warn(`Navigation: could not add a tile (${Recast.statusToReadableString(status)}). The loaded world may need more than ${1 << TILE_BITS} tiles; use a larger tileSize.`);
        warnedFull = true;
      }
    }
    built.set(tileKey(tile.x, tile.y), tile);
  }

  /** Builds queued tiles until `budget` milliseconds have passed. Always builds at least one. */
  function buildTiles(budget: number): void {
    if (!job) return;
    const deadline = performance.now() + budget;
    do {
      const tile = job.queue.pop();
      if (!tile) break;
      buildTile(job, tile);
    } while (performance.now() < deadline);
    for (const agent of agents) agent.recover(); // there may be ground now under agents that had none
    if (job.queue.length === 0) finishJob();
  }
  function freeJob(): void {
    job?.vertices.destroy();
    job = null;
  }
  function finishJob(): void {
    freeJob();
    ready = true;
    refreshDebugMesh();
    api.onRebuilt?.();
    if (requested) return; // a newer request is waiting its turn
    for (const resolve of waiting.splice(0)) resolve();
  }

  function refreshDebugMesh(): void {
    if (debugMesh) {
      debugMesh.removeFromParent();
      debugMesh.geometry.dispose();
      debugMesh = null;
    }
    if (!debugScene || built.size === 0) return;
    const [positions, indices] = Recast.getNavMeshPositionsAndIndices(navMesh);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(indices);
    // Drawn over everything: paving lies a few centimetres over the ground the navmesh was made from, and would hide it.
    const material = new THREE.MeshBasicNodeMaterial({ color: 0x1a66ff, transparent: true, opacity: 0.35, depthWrite: false, depthTest: false, side: THREE.DoubleSide });
    debugMesh = new THREE.Mesh(geometry, material);
    debugMesh.name = "navmesh-debug";
    debugScene.add(debugMesh);
  }

  // --- Queries and agents ------------------------------------------------------

  function closestPoint(position: THREE.Vector3): THREE.Vector3 | null {
    const { success, polyRef, point } = query.findClosestPoint(position);
    return success && polyRef !== 0 ? new THREE.Vector3(point.x, point.y, point.z) : null;
  }

  function addAgent(start: THREE.Vector3, agentOptions: NavAgentOptions = {}): NavAgent {
    const radius = agentOptions.radius ?? settings.agentRadius;
    const position = closestPoint(start) ?? start.clone();
    const velocity = new THREE.Vector3();
    const raw = crowd.addAgent(position, {
      radius,
      height: agentOptions.height ?? settings.agentHeight,
      maxSpeed: agentOptions.speed ?? 2,
      maxAcceleration: agentOptions.acceleration ?? 8,
      collisionQueryRange: radius * 12,
      pathOptimizationRange: radius * 30,
      separationWeight: 2,
      updateFlags: AGENT_UPDATE_FLAGS,
      obstacleAvoidanceType: 0,
      queryFilterType: 0,
    });
    /** Where the agent has been told to go, kept in case it loses its footing on the way (see `recover`). */
    let target: THREE.Vector3 | null = null;
    let removed = false;

    const agent = {
      position,
      velocity,
      get speed() {
        return raw.maxSpeed;
      },
      set speed(value: number) {
        if (!removed && value !== raw.maxSpeed) raw.maxSpeed = value;
      },
      goTo(destination: THREE.Vector3): boolean {
        const reachable = removed ? null : closestPoint(destination);
        if (!reachable) return false;
        target = reachable;
        raw.requestMoveTarget(reachable);
        return true;
      },
      stop(): void {
        target = null;
        if (!removed) raw.resetMoveTarget();
      },
      teleport(destination: THREE.Vector3): void {
        if (removed) return;
        target = null;
        position.copy(closestPoint(destination) ?? destination);
        velocity.set(0, 0, 0);
        raw.teleport(position);
      },
      remove(): void {
        if (removed) return;
        removed = true;
        agents.delete(agent);
        crowd.removeAgent(raw);
      },
      /** An agent with no navmesh under it stands where it is; once there is ground again it steps back on and takes up its errand. */
      recover(): void {
        if (raw.state() !== 0) return; // 0 is Detour's "not on the navmesh"
        const ground = closestPoint(position);
        if (!ground) return;
        raw.teleport(ground);
        if (target) raw.requestMoveTarget(target);
      },
      update(): void {
        const at = raw.position();
        position.set(at.x, at.y, at.z);
        if (raw.state() === 0) velocity.set(0, 0, 0);
        else {
          const moving = raw.velocity();
          velocity.set(moving.x, moving.y, moving.z);
        }
      },
    };
    agents.add(agent);
    return agent;
  }

  /** Spends up to `budget` milliseconds on bringing the navmesh up to date. */
  function work(budget: number): void {
    if (requested && performance.now() - requestedAt >= REBUILD_SETTLE_TIME) {
      const sources = requested;
      requested = null;
      timed("navigation: plan", () => startJob(sources)); // tiles start on the next call
    } else if (job) {
      timed("navigation: tiles", () => buildTiles(budget));
    }
  }
  /** While nothing is being drawn there is no frame loop to pace the building (and nothing on screen to stall): a timer does it, in longer slices. */
  function workOffscreen(): void {
    offscreenTimer = undefined;
    if (!job && !requested) return;
    const rendering = performance.now() - lastFrameAt < NOT_RENDERING_AFTER;
    if (!rendering) work(OFFSCREEN_BUDGET);
    offscreenTimer = setTimeout(workOffscreen, rendering ? NOT_RENDERING_AFTER : 0);
  }

  const api: Navigation = {
    get ready() {
      return ready;
    },
    onRebuilt: null,
    rebuild(sources) {
      if (!requested) requestedAt = performance.now();
      requested = sources;
      offscreenTimer ??= setTimeout(workOffscreen, 0);
      return new Promise((resolve) => waiting.push(resolve));
    },
    findPath(from, to) {
      const { success, path } = query.computePath(from, to);
      return success ? path.map((p) => new THREE.Vector3(p.x, p.y, p.z)) : [];
    },
    closestPoint,
    randomPointAround(center, radius) {
      const { success, randomPolyRef, randomPoint } = query.findRandomPointAroundCircle(center, radius);
      return success && randomPolyRef !== 0 ? new THREE.Vector3(randomPoint.x, randomPoint.y, randomPoint.z) : null;
    },
    addAgent,
    showDebug(scene) {
      debugScene = scene;
      refreshDebugMesh();
    },
    update(dt, from) {
      eye.copy(from);
      lastFrameAt = performance.now();
      work(settings.frameBudget);
      if (dt <= 0) return; // paused
      crowd.update(dt);
      // Now and then, see whether an agent left without ground (added before the navmesh was there) has some now.
      if (frame++ % 30 === 0) for (const agent of agents) agent.recover();
      for (const agent of agents) agent.update();
    },
    get stats() {
      return { tiles: built.size, queued: job?.queue.length ?? 0 };
    },
  };
  return api;
}
