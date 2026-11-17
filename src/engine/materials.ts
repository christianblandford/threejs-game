import * as THREE from "three/webgpu";
import { Fn, float, instanceIndex, length, positionLocal, positionPrevious, sin, time, uv, vec2, vec3 } from "three/tsl";
import { surface } from "./surface";

/**
 * What the map's materials need beyond what glTF can say. The Blender tools
 * mark materials with custom properties (`wind`, `foliage`, `matte`, `gain`,
 * `metres`), which arrive here as `userData`; the ground's worn paths are a
 * second material blended in through a mask image the manifest names; and
 * anything called `Water` or `Sea` is replaced by water made here.
 *
 * Written once, as shader nodes: three compiles them for WebGPU and WebGL
 * alike. (The Babylon version of each of these is written twice.)
 */

export interface MaterialExtras {
  wind?: number;
  foliage?: boolean;
  matte?: boolean;
  /** Multiplies the material's colour. */
  gain?: number;
  /** How much ground one repeat of the texture covers (m). Marks a tiled surface. */
  metres?: number;
  /** Strength of the blotches that break a tiled surface up, 0 to 1 (default 0.55). */
  variation?: number;
  /** 0 to 1: also show a shifted copy of the texture in patches and a larger one with distance. Organic surfaces only. */
  detile?: number;
}

export interface Blend {
  /** The material blended into, and the one that shows where the mask is white. */
  material: string;
  layer: string;
  mask: THREE.Texture;
  /** Mask u = x * map[0] + map[2], v = z * map[1] + map[3], in the Babylon game's space (x negated). */
  map: [number, number, number, number];
}

/**
 * How far a leaf tip moves at the height of a gust (m), and which way the
 * wind blows. Gentle and slow on purpose: temporal anti-aliasing is not told
 * how fast swaying leaves move (see `displaced` below), so anything that
 * travels more than a fraction of a pixel a frame is blended with where it
 * was and smears. Half the Babylon game's strength, and no quick flutter.
 */
export const WIND = { strength: 0.045, direction: new THREE.Vector2(0.8, 0.6) };

/** A node material with everything a loaded standard material has. (What the renderer does for itself, done early so nodes can be added.) */
function asNodeMaterial(material: THREE.Material): THREE.MeshStandardNodeMaterial {
  if ((material as THREE.MeshStandardNodeMaterial).isNodeMaterial) return material as THREE.MeshStandardNodeMaterial;
  const node = (material as THREE.MeshPhysicalMaterial).isMeshPhysicalMaterial ? new THREE.MeshPhysicalNodeMaterial() : new THREE.MeshStandardNodeMaterial();
  const source = material as unknown as Record<string, unknown>;
  const target = node as unknown as Record<string, unknown>;
  for (const key in material) {
    if (key !== "uuid" && key !== "type") target[key] = source[key];
  }
  return node;
}

/**
 * Sway, from the second UV map of anything grown by the Blender plant tools:
 * u is how much a vertex moves (0 at the foot of a trunk, 1 at a leaf), v a
 * phase shared along one branch. A slow gust rolls over the land with a
 * quicker flutter on top.
 */
function sway(amount: number) {
  const data = uv(1);
  const weight = data.x.mul(WIND.strength * amount);
  const phase = data.y.mul(6.2831853);
  // Each placing of an instanced plant gets its own place in the gust.
  const where = float(instanceIndex).mul(1.37);
  const gust = sin(time.mul(0.55).add(where)).mul(0.45).add(0.55);
  const flutter = sin(time.mul(1.3).add(phase).add(positionLocal.x.mul(0.37)));
  const push = vec2(WIND.direction.x, WIND.direction.y).mul(gust.mul(flutter.mul(0.3).add(0.7))).mul(weight);
  return displaced(vec3(push.x, length(push).mul(-0.3).mul(data.x), push.y));
}

/**
 * A vertex moved in the shader, for a material's `positionNode`.
 *
 * The move is made to the vertex's *previous* position as well. three works
 * out motion vectors (for temporal anti-aliasing) from where a vertex is now
 * against where it was, and "where it was" is the untouched mesh: move only
 * the current position and every swaying leaf appears to jump by its whole
 * sway each frame. The anti-aliasing then looks for last frame's leaf in the
 * wrong place, finds something else, discards its history for that pixel,
 * and the picture never settles: grass and trees vibrate, with the camera
 * and the wind both still. Moved together, the sway itself counts as no
 * motion, which at these speeds is close enough.
 */
export function displaced(offset: ReturnType<typeof vec3>) {
  return Fn(() => {
    const by = offset.toVar();
    positionPrevious.addAssign(by);
    return positionLocal.add(by);
  })();
}

/** Called once for each of the map's materials, by name. */
/** How far textures are filtered along the line of sight (1 is not at all; GPUs go to 16). */
const ANISOTROPY = 8;

export function upgradeMaterial(material: THREE.Material, extras: MaterialExtras, blend: Blend | null, layer: THREE.MeshStandardMaterial | null): THREE.Material {
  const standard = material as THREE.MeshStandardMaterial;
  if (extras.gain && standard.color) standard.color.multiplyScalar(extras.gain);
  // Ground and walls are mostly seen at a shallow angle, where a texture
  // read the ordinary way is blurred along the line of sight: cobbles turn
  // to a flat colour ten metres off. Anisotropic filtering reads it properly.
  // three's default is none. (Not for leaves: their cards are cut out by the
  // texture, and it thickens the cut.)
  if (!extras.foliage) {
    for (const map of [standard.map, standard.normalMap, standard.roughnessMap, standard.metalnessMap, standard.aoMap, standard.emissiveMap]) {
      if (map) map.anisotropy = ANISOTROPY;
    }
  }
  if (extras.matte || extras.foliage) {
    // Leaves cut down to flat facets mirror the sky like plastic otherwise.
    standard.roughness = 1;
    standard.metalness = 0;
    standard.envMapIntensity = 0.45;
  }

  if (extras.wind && new URLSearchParams(location.search).get("wind") !== "0") {
    const node = asNodeMaterial(material);
    node.positionNode = sway(extras.wind);
    material = node;
  }

  // A tiled surface: break up the repeat, and blend in a second surface where the map says so.
  if (extras.metres && standard.map && !extras.foliage && !standard.transparent && !standard.alphaTest) {
    const node = asNodeMaterial(material);
    surface(node, standard, extras, blend && layer?.map ? { ...blend, layer } : null);
    material = node;
  }
  return material;
}
