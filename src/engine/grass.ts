import * as THREE from "three/webgpu";
import { Fn, attribute, cameraPosition, cameraProjectionMatrix, cos, distance, dot, float, fract, max, mix, positionGeometry, positionPrevious, screenSize, sin, smoothstep, time, transformNormalToView, uv, vec2, vec3 } from "three/tsl";
import { WIND } from "./materials";

/**
 * Grass, planted on the map's ground wherever its Blender author painted it:
 * the ground mesh carries how thickly it grows in its second UV map (the
 * `ground_cover` vertex group), and its vertex colours say how lush or dry
 * the land is there, which the tufts take as their own colour.
 *
 * Each piece of ground is planted twice over, as the Babylon game's is: a
 * thin sward of broad blades seen out to `GRASS.distance`, and a much thicker
 * one of fine blades that only appears close by, where the eye can tell.
 * Neither ends at a line: toward the end of its range each tuft sinks into
 * its own root, some sooner than others, so the grass thins out instead of
 * stopping and never pops in as you walk.
 *
 * The ground is cut into square patches so only those near the camera are
 * drawn. A patch is one draw call: a tuft drawn once per placing, each
 * placing 28 bytes (where, which way round, how big, what colour) that the
 * vertex shader turns into a tuft. An `InstancedMesh` would carry a whole
 * matrix per tuft and could not say where a tuft's root is.
 */

export const GRASS = {
  /** Tufts per square metre of the thin sward, where cover is full. */
  density: 8,
  /** The thick sward adds this many times as much again. */
  nearBoost: 2.8,
  /** How far each is drawn (m from the camera). */
  distance: 48,
  nearDistance: 16,
  /** Size of the patches each is cut into (m). */
  patch: 16,
  nearPatch: 8,
};

/** The close-up sward is many fine blades spread wide, so neighbouring tufts run together; the distant one is fewer, broader blades that still read from far off. */
const NEAR_TUFT = { blades: 9, spread: 0.13, wide: 1 };
const FAR_TUFT = { blades: 5, spread: 0.08, wide: 1.7 };
/**
 * The grass's own colour, before the ground's tint (which it takes twice
 * over, as the ground does): about as bright as the ground texture, and
 * greener. Darker, and thin grass is so many dark strokes on a pale lawn;
 * much brighter, and every blade is outlined.
 */
const COLOUR = new THREE.Color(0.33, 0.4, 0.17);
/** Tints are stored in a byte each, and a tuft may be a little lighter than its ground: so they are stored at this scale. */
const TINT_SCALE = 0.8;
/** Painted cover below this grows nothing. */
const BARE = 0.12;
/** How the number of tufts falls off with the painted cover (as a power of it): well under 1, so thin cover is many short tufts. */
const THIN = 0.35;
/** A blade is never drawn narrower than this many pixels: thinner, it is caught by some frames and missed by others, and a field of them shimmers. */
const MIN_PIXELS = 1.5;

export interface GrassPatch {
  mesh: THREE.Mesh;
  /** Its centre on the ground. */
  x: number;
  z: number;
  /** Half its width, and how far from the camera its grass is drawn at all. */
  half: number;
  reach: number;
  count: number;
}

type Tier = "near" | "far";
const tufts: Partial<Record<Tier, THREE.BufferGeometry>> = {};
const materials: Partial<Record<Tier, THREE.Material>> = {};

/**
 * One tuft: a few blades leaning out from a point, each a narrow strip that
 * tapers to a tip. `position` is the blade's centre line; `side` says which
 * way is across the blade (x, z) and how far out this vertex sits (half the
 * blade's width), so the shader can keep a far blade a pixel wide. `uv` is
 * the blade's phase in the wind and the height up it.
 */
function tuftGeometry(tier: Tier): THREE.BufferGeometry {
  const made = tufts[tier];
  if (made) return made;
  const shape = tier === "near" ? NEAR_TUFT : FAR_TUFT;
  let seed = 99;
  const random = (): number => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const positions: number[] = [];
  const sides: number[] = [];
  const uvs: number[] = [];
  const normals: number[] = [];
  const indices: number[] = [];
  for (let blade = 0; blade < shape.blades; blade++) {
    const phase = random();
    const turn = (blade / shape.blades) * Math.PI * 2 + random() * 0.8;
    const lean = 0.025 + random() * 0.09; // how far the tip ends up from the root
    const tall = 0.09 + random() * 0.08;
    const wide = (0.0035 + random() * 0.003) * shape.wide; // real blades are narrow: wide ones read as a succulent
    const outX = Math.cos(turn);
    const outZ = Math.sin(turn);
    const from = shape.spread * (0.25 + random() * 0.75);
    const base = positions.length / 3;
    // Root pair, middle pair, tip: the middle gives a close blade its curve. The
    // distant sward does without it, one triangle a blade: most of the grass
    // drawn is that sward, and at its distance a blade is a few pixels.
    const levels = tier === "near" ? ([[0, 0, wide], [0.55, 0.35, wide * 0.75], [1, 1, 0]] as const) : ([[0, 0, wide], [1, 1, 0]] as const);
    for (const [up, out, half] of levels) {
      const x = outX * (from + lean * out);
      const z = outZ * (from + lean * out);
      for (const across of half > 0 ? [-1, 1] : [0]) {
        positions.push(x, tall * up, z);
        sides.push(-outZ * across, outX * across, half);
        uvs.push(phase, up);
        // Lit as the ground is: a field of blades each lit by its own facing is noise.
        normals.push(0, 1, 0);
      }
    }
    if (tier === "near") indices.push(base, base + 1, base + 2, base + 1, base + 3, base + 2, base + 2, base + 3, base + 4);
    else indices.push(base, base + 1, base + 2);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("side", new THREE.Float32BufferAttribute(sides, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  geometry.setIndex(indices);
  tufts[tier] = geometry;
  return geometry;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Node = any;

function tuftMaterial(tier: Tier): THREE.Material {
  const made = materials[tier];
  if (made) return made;
  const grass = new THREE.MeshStandardNodeMaterial({ roughness: 0.95, metalness: 0, side: THREE.DoubleSide });
  
  const reach = tier === "near" ? GRASS.nearDistance : GRASS.distance;
  // The ragged edge is half the fade's width either way, so start well inside the limit.
  const from = reach * 0.55;
  const to = reach * 0.85;
  const still = new URLSearchParams(location.search).get("wind") === "0";

  const root: Node = attribute("root", "vec4"); // where it stands, and which way round
  const size: Node = attribute("shape", "vec2"); // across, and up
  const tint: Node = attribute("tint", "vec4");
  const side: Node = attribute("side", "vec3");
  const phase: Node = uv().x;
  const height: Node = uv().y;

  grass.positionNode = Fn(() => {
    const away: Node = distance(root.xz, cameraPosition.xz);
    const ragged: Node = fract(sin(dot(root.xz, vec2(12.9898, 78.233))).mul(43758.5453));
    const kept: Node = float(1).sub(smoothstep(from, to, away.add(ragged.sub(0.5).mul(to - from))));
    // How much ground one pixel covers at this distance, from the camera's own projection.
    const pixel: Node = away.mul(2).div((cameraProjectionMatrix as Node).element(1).element(1).mul(screenSize.y));
    const half: Node = max(side.z.mul(size.x), pixel.mul(MIN_PIXELS / 2)).mul(side.z.greaterThan(0).select(1, 0));
    const centre: Node = positionGeometry.mul(vec3(size.x, size.y, size.x));
    const own: Node = centre.add(vec3(side.x, 0, side.y).mul(half));
    const c: Node = cos(root.w);
    const s: Node = sin(root.w);
    const turned: Node = vec3(own.x.mul(c).add(own.z.mul(s)), own.y, own.z.mul(c).sub(own.x.mul(s)));
    // One slow sway that rolls over the field, no flutter (see WIND in materials.ts for why).
    const gust: Node = sin(time.mul(0.6).add(root.x.mul(0.31)).add(root.z.mul(0.23)).add(phase.mul(1.7))).mul(0.5).add(0.5);
    const bend: Node = still ? float(0) : height.mul(height).mul(gust.mul(0.028).add(0.004));
    const swayed: Node = turned.add(vec3(bend.mul(WIND.direction.x), bend.mul(-0.25), bend.mul(WIND.direction.y)));
    const world: Node = root.xyz.add(swayed.mul(kept));
    // Where it was a frame ago is where it is now: the sway counts as no motion (see `displaced`).
    positionPrevious.assign(world);
    return world;
  })();

  // Lit as the ground is, whichever way a blade faces. Left to itself a
  // two-sided material turns the normal round on faces seen from behind:
  // with every normal pointing up, that is half the blades lit from below,
  // and a sward that is darker than the ground it grows on.
  grass.normalNode = transformNormalToView(vec3(0, 1, 0));

  // Darker at the root, where blades shade each other, but not much darker than the ground: a near-black root turns every tuft into a dot.
  const shade: Node = mix(0.68, 1, height);
  const painted: Node = tint.rgb.div(TINT_SCALE);
  grass.colorNode = vec3(COLOUR.r, COLOUR.g, COLOUR.b).mul(painted).mul(painted).mul(shade);
  materials[tier] = grass;
  return grass;
}

/** One patch's placings while they are being collected: seven numbers a tuft, in a buffer that doubles when full (plain arrays of this size keep the garbage collector busy mid-frame). */
interface Cell {
  x: number;
  z: number;
  /** x, y, z, turn, across, up per tuft. */
  placed: Float32Array;
  tints: Uint8Array;
  count: number;
  low: number;
  high: number;
}

function room(cell: Cell): void {
  if (cell.count * 6 < cell.placed.length) return;
  const placed = new Float32Array(cell.placed.length * 2);
  placed.set(cell.placed);
  cell.placed = placed;
  const tints = new Uint8Array(cell.tints.length * 2);
  tints.set(cell.tints);
  cell.tints = tints;
}

/**
 * Plants one piece of ground. Written as steps (it yields every so often) so
 * the map's set-up can do a little each frame: a field is tens of thousands
 * of tufts. Returns its patches, none of them shown yet.
 */
export function* plantGrass(ground: THREE.Mesh, seedFrom: number, amount = 1): Generator<void, GrassPatch[]> {
  const geometry = ground.geometry;
  const position = geometry.attributes.position as THREE.BufferAttribute | undefined;
  const weight = geometry.attributes.uv1 as THREE.BufferAttribute | undefined;
  const colour = geometry.attributes.color as THREE.BufferAttribute | undefined;
  const index = geometry.index;
  const density = GRASS.density * amount;
  if (!position || !index || density <= 0) return [];

  let seed = (seedFrom * 2654435761) % 2147483647 || 1;
  const random = (): number => (seed = (seed * 16807) % 2147483647) / 2147483647;
  // Weights are painted per vertex and blend across each triangle, so a road's
  // bare strip has a fringe of small values a metre or two wide. Nothing grows
  // below `BARE`: no stray blades coming up through paving.
  const thickness = (i: number): number => (weight ? Math.max(0, (weight.getX(i) - BARE) / (1 - BARE)) : 1);
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const cells: Record<Tier, Map<number, Cell>> = { near: new Map(), far: new Map() };
  const share = GRASS.nearBoost / (1 + GRASS.nearBoost);
  let sinceYield = 0;

  for (let t = 0; t < index.count; t += 3) {
    const ia = index.getX(t), ib = index.getX(t + 1), ic = index.getX(t + 2);
    const coverA = thickness(ia), coverB = thickness(ib), coverC = thickness(ic);
    const most = Math.max(coverA, coverB, coverC);
    if (most <= 0) continue;
    a.fromBufferAttribute(position, ia).applyMatrix4(ground.matrixWorld);
    b.fromBufferAttribute(position, ib).applyMatrix4(ground.matrixWorld);
    c.fromBufferAttribute(position, ic).applyMatrix4(ground.matrixWorld);
    // Area as seen from above: a steep bank grows no more grass than its footprint.
    const area = Math.abs((b.x - a.x) * (c.z - a.z) - (c.x - a.x) * (b.z - a.z)) / 2;
    // Thin cover is short grass, not a few tall tufts: the count falls off
    // slowly with the paint and the height takes the rest.
    const expected = area * density * (1 + GRASS.nearBoost) * most ** THIN;
    const tries = Math.floor(expected) + (random() < expected % 1 ? 1 : 0);
    for (let n = 0; n < tries; n++) {
      let u = random(), v = random();
      if (u + v > 1) {
        u = 1 - u;
        v = 1 - v;
      }
      const w = 1 - u - v;
      // Thin out toward bare ground.
      const cover = coverA * w + coverB * u + coverC * v;
      if (random() * most ** THIN > cover ** THIN) continue;
      const lush = 0.7 + 0.5 * cover;
      const x = a.x * w + b.x * u + c.x * v;
      const y = a.y * w + b.y * u + c.y * v;
      const z = a.z * w + b.z * u + c.z * v;
      // Most tufts belong to the thicker, close-up sward and are a little smaller.
      const tier: Tier = random() < share ? "near" : "far";
      const patch = tier === "near" ? GRASS.nearPatch : GRASS.patch;
      const cx = Math.floor(x / patch), cz = Math.floor(z / patch);
      const key = (cx + 32768) * 65536 + (cz + 32768);
      let cell = cells[tier].get(key);
      if (!cell) cells[tier].set(key, (cell = { x: (cx + 0.5) * patch, z: (cz + 0.5) * patch, placed: new Float32Array(6 * 256), tints: new Uint8Array(4 * 256), count: 0, low: Infinity, high: -Infinity }));
      room(cell);
      // Much of a size: a sward of sprigs with the odd clump the height of the fox reads as weeds on bare ground.
      const across = (0.8 + random() * 0.5) * (tier === "near" ? 0.9 : 1.1);
      const up = across * lush * (0.85 + random() ** 2 * 0.4);
      const at = cell.count * 6;
      cell.placed[at] = x;
      cell.placed[at + 1] = y - 0.02;
      cell.placed[at + 2] = z;
      cell.placed[at + 3] = random() * Math.PI * 2;
      cell.placed[at + 4] = across;
      cell.placed[at + 5] = up;
      cell.low = Math.min(cell.low, y);
      cell.high = Math.max(cell.high, y);
      // The ground's tint here, a shade lighter or darker.
      const light = (0.82 + random() * 0.36) * TINT_SCALE * 255;
      const tint = cell.count * 4;
      cell.tints[tint] = colour ? Math.min(255, (colour.getX(ia) * w + colour.getX(ib) * u + colour.getX(ic) * v) * light) : light;
      cell.tints[tint + 1] = colour ? Math.min(255, (colour.getY(ia) * w + colour.getY(ib) * u + colour.getY(ic) * v) * light) : light;
      cell.tints[tint + 2] = colour ? Math.min(255, (colour.getZ(ia) * w + colour.getZ(ib) * u + colour.getZ(ic) * v) * light) : light;
      cell.tints[tint + 3] = 255;
      cell.count++;
      // About a millisecond and a half of planting between pauses.
      if (++sinceYield >= 400) {
        sinceYield = 0;
        yield;
      }
    }
  }

  const patches: GrassPatch[] = [];
  for (const tier of ["far", "near"] as const) {
    const patch = tier === "near" ? GRASS.nearPatch : GRASS.patch;
    const reach = tier === "near" ? GRASS.nearDistance : GRASS.distance;
    for (const cell of cells[tier].values()) {
      const count = cell.count;
      const tuft = tuftGeometry(tier);
      const placed = new THREE.InstancedBufferGeometry();
      placed.index = tuft.index;
      for (const name of ["position", "side", "uv", "normal"]) placed.setAttribute(name, tuft.attributes[name]!);
      // Where, which way round and how big, side by side in one buffer.
      const placings = new THREE.InstancedInterleavedBuffer(cell.placed.slice(0, count * 6), 6);
      placed.setAttribute("root", new THREE.InterleavedBufferAttribute(placings, 4, 0));
      placed.setAttribute("shape", new THREE.InterleavedBufferAttribute(placings, 2, 4));
      placed.setAttribute("tint", new THREE.InstancedBufferAttribute(cell.tints.slice(0, count * 4), 4, true));
      placed.instanceCount = count;
      // The tuft's own corners say nothing about where its placings are.
      const middle = (cell.low + cell.high) / 2;
      placed.boundingSphere = new THREE.Sphere(new THREE.Vector3(cell.x, middle, cell.z), Math.hypot(patch * 0.71, (cell.high - cell.low) / 2 + 1.2));
      placed.boundingBox = new THREE.Box3(new THREE.Vector3(cell.x - patch / 2 - 0.3, cell.low - 0.1, cell.z - patch / 2 - 0.3), new THREE.Vector3(cell.x + patch / 2 + 0.3, cell.high + 1.2, cell.z + patch / 2 + 0.3));
      const mesh = new THREE.Mesh(placed, tuftMaterial(tier));
      mesh.name = `grass:${ground.name}:${tier}:${patches.length}`;
      mesh.castShadow = false;
      mesh.receiveShadow = true;
      mesh.matrixAutoUpdate = false;
      mesh.visible = false; // until the camera is near
      patches.push({ mesh, x: cell.x, z: cell.z, half: patch / 2, reach, count });
      yield;
    }
  }
  return patches;
}

/** Frees a patch's own list of placings. (The tuft itself is shared.) */
export function uproot(patch: GrassPatch): void {
  const geometry = patch.mesh.geometry;
  // Disposing a geometry frees every buffer it names, and the tuft's are
  // shared with every other patch: take those off first, so only this
  // patch's own placings go.
  for (const name of ["position", "side", "uv", "normal"]) geometry.deleteAttribute(name);
  geometry.index = null;
  patch.mesh.removeFromParent();
  geometry.dispose();
}
