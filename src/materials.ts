import * as THREE from "three/webgpu";
import { Fn, cameraPosition, cross, distance, float, instanceIndex, length, materialNormal, max, mix, normalWorldGeometry, normalize, positionLocal, positionPrevious, positionWorld, sin, smoothstep, texture, time, transformNormalToView, uniform, uv, vec2, vec3, vertexColor } from "three/tsl";

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

const VARIATION_STRENGTH = 0.55;
/** How strongly the ground's close-up grain shows, 0 to 1. */
const GRAIN = 0.9;
let cloudy: THREE.DataTexture | null = null;

/**
 * A soft, cloudy greyscale pattern that repeats seamlessly. Laid over a
 * tiled texture at a much larger scale, it lightens some repeats and darkens
 * others, so the eye stops finding the grid. (A texture, not shader noise:
 * it is read four times a pixel on the ground.)
 */
function variationTexture(): THREE.DataTexture {
  if (cloudy) return cloudy;
  const size = 256;
  let state = 12345;
  const random = (): number => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0xffffffff;
  };
  // A sum of waves that each fit the square a whole number of times: seamless.
  const waves = Array.from({ length: 18 }, () => {
    const frequency = 1 + Math.floor(random() * 5);
    const angle = random() * Math.PI * 2;
    return { kx: Math.round(Math.cos(angle) * frequency), ky: Math.round(Math.sin(angle) * frequency), phase: random() * Math.PI * 2, height: 1 / frequency };
  });
  const values = new Float32Array(size * size);
  let lowest = Infinity;
  let highest = -Infinity;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let value = 0;
      for (const wave of waves) value += Math.sin(((wave.kx * x + wave.ky * y) / size) * Math.PI * 2 + wave.phase) * wave.height;
      values[y * size + x] = value;
      lowest = Math.min(lowest, value);
      highest = Math.max(highest, value);
    }
  }
  const pixels = new Uint8Array(size * size * 4);
  for (let i = 0; i < values.length; i++) {
    const grey = Math.round(((values[i]! - lowest) / (highest - lowest)) * 255); // about half on average
    pixels.set([grey, grey, grey, 255], i * 4);
  }
  cloudy = new THREE.DataTexture(pixels, size, size, THREE.RGBAFormat);
  cloudy.wrapS = cloudy.wrapT = THREE.RepeatWrapping;
  cloudy.magFilter = THREE.LinearFilter;
  cloudy.minFilter = THREE.LinearMipmapLinearFilter;
  cloudy.generateMipmaps = true;
  cloudy.colorSpace = THREE.NoColorSpace;
  cloudy.needsUpdate = true;
  return cloudy;
}

/**
 * What a big tiled surface needs that the stock material has no setting for
 * (the Babylon game's `surfacePlugin.ts`, written once here as nodes).
 *
 * Breaking up repetition, strongest last: blotches a few repeats wide,
 * lighter and darker, warmer and cooler; a second copy of the texture,
 * shifted, shown in irregular patches (`detile`); and with distance a much
 * larger copy taking over, because a grid shows most from far away.
 *
 * Blending in a second surface through a mask: a path worn into grass. The
 * boundary follows the second surface's own texture, so earth breaks into
 * grass in clumps instead of fading across a ruled line, and where the
 * second surface shows it brings its own relief.
 */
function surface(node: THREE.MeshStandardNodeMaterial, standard: THREE.MeshStandardMaterial, extras: MaterialExtras, blend: (Omit<Blend, "layer"> & { layer: THREE.MeshStandardMaterial }) | null): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type N = any;
  const noise = variationTexture();
  const amount = extras.variation ?? VARIATION_STRENGTH;
  const detile = extras.detile ?? 0;
  const map = standard.map!;
  const world: N = positionWorld;
  // Height counts too, so a wall is not striped top to bottom.
  const ground: N = world.xz.add(world.yy.mul(vec2(0.37, 0.61)));
  const light: N = texture(noise, ground.mul(0.031)).r;
  const hue: N = texture(noise, ground.mul(0.0113).add(vec2(0.43, 0.17))).r;

  // The ground takes its painted tint twice (once here, once when three multiplies vertex colours in): what gives a meadow its green.
  let own: N = blend ? texture(map, uv()).rgb.mul(vertexColor().rgb) : texture(map, uv()).rgb;
  if (detile > 0) {
    const shifted: N = texture(map, uv().add(vec2(0.413, 0.287))).rgb;
    const pick: N = smoothstep(0.4, 0.6, texture(noise, uv().mul(0.173).add(vec2(0.7, 0.2))).r);
    const wide: N = texture(map, uv().mul(0.217).add(vec2(0.31, 0.77))).rgb;
    const far: N = smoothstep(25, 90, distance(cameraPosition, world));
    own = mix(own, mix(mix(own, shifted, pick), wide, far.mul(0.85)), detile);
  }
  let colour: N = own.mul(uniform(standard.color));
  const close: N = smoothstep(14, 3, distance(cameraPosition, world)).mul(GRAIN);
  if (blend) {
    // Up close a ground texture is a blur of a few texels a blade: the same
    // texture laid much smaller, its fine grain alone (against its own
    // average), gives the eye something at its feet. Gone by a few metres,
    // where the texture itself is sharp enough.
    const fine: N = uv().mul(6.3);
    const grain: N = texture(map, fine).g.div(texture(map, fine).bias(float(5)).g.add(0.02));
    colour = colour.mul(mix(1, grain.clamp(0.35, 2), close));
  }
  colour = colour.mul(light.sub(0.5).mul(1.1 * amount).add(1));
  colour = colour.mul(hue.sub(0.5).mul(vec3(0.5, 0.05, -0.55)).mul(amount).add(1));

  if (blend) {
    // The map's numbers are in the Babylon game's space: x negated.
    const gameX: N = world.x.negate();
    // Read four times around the point: one reading follows the mask's own pixels, and a narrow edge then shows them as steps.
    const maskUV: N = vec2(gameX.mul(blend.map[0]).add(blend.map[2]), world.z.mul(blend.map[1]).add(blend.map[3]));
    const step = 0.75 / ((blend.mask.image as { width?: number } | undefined)?.width ?? 1024);
    const mask: N = texture(blend.mask, maskUV.add(vec2(step, step))).r
      .add(texture(blend.mask, maskUV.add(vec2(-step, step))).r)
      .add(texture(blend.mask, maskUV.add(vec2(step, -step))).r)
      .add(texture(blend.mask, maskUV.add(vec2(-step, -step))).r)
      .mul(0.25);
    const layerUV: N = vec2(gameX, world.z).div((blend.layer.userData as MaterialExtras).metres ?? 2);
    const layerMap = blend.layer.map!;
    const layer: N = mix(texture(layerMap, layerUV).rgb, texture(layerMap, layerUV.mul(0.23).add(vec2(0.6, 0.1))).rgb, 0.35);
    // The layer's own brightness (as the eye sees it, not as light) pushes the boundary in and out.
    // Two sizes of it: clumps a couple of metres across, and a ragged fringe a hand wide.
    const clumps: N = texture(noise, world.xz.mul(0.41)).r.add(texture(layerMap, layerUV.mul(1.7)).g.pow(0.4545)).sub(1);
    const ragged: N = texture(noise, world.xz.mul(2.9).add(vec2(0.37, 0.11))).r.sub(0.5);
    const weight: N = smoothstep(0.41, 0.59, mask.add(clumps.mul(0.5).add(ragged.mul(0.3)).mul(mask.mul(2).sub(1).abs().oneMinus())));
    // The ground's painted tint is multiplied in after this node, over everything: taken out of the earth first, which is not tinted.
    const tint: N = max(vertexColor().rgb, 0.05);
    // The earth gets the same close-up grain as the grass, and stones' worth of lighter and darker at arm's length.
    const earthFine: N = layerUV.mul(4.7);
    const earthGrain: N = texture(layerMap, earthFine).g.div(texture(layerMap, earthFine).bias(float(5)).g.add(0.02));
    const pebbled: N = texture(noise, world.xz.mul(1.3).add(vec2(0.21, 0.63))).r.sub(0.5).mul(0.5).add(1);
    const earth: N = layer.mul(uniform(blend.layer.color)).mul(light.sub(0.5).mul(1.1 * amount).add(1)).mul(pebbled).mul(mix(1, earthGrain.clamp(0.35, 2), close));
    colour = mix(colour, earth.div(tint), weight);
    if (blend.layer.normalMap) {
      // The frame the layer's relief is laid in is built from the surface
      // normal, with "across" the way its texture runs: toward -x here, and
      // green toward -z ("up" in a glTF image is toward smaller v).
      const relief: N = texture(blend.layer.normalMap, layerUV).xyz.mul(2).sub(1);
      const up: N = normalWorldGeometry;
      const across: N = normalize(cross(vec3(0, 0, 1), up).add(vec3(-0.0001, 0, 0)));
      const along: N = cross(across, up);
      const laid: N = normalize(across.mul(relief.x).add(along.mul(relief.y)).add(up.mul(relief.z)));
      node.normalNode = normalize(mix(materialNormal, transformNormalToView(laid), weight));
    }
  }
  node.colorNode = colour;
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

/** The map's baked terrain heights, as its manifest describes them, and the image itself. */
export interface WaterDepth {
  heights: THREE.Texture;
  size: number;
  /** u = x * map[0] + map[2], v = z * map[1] + map[3], with x and z in the map's own (left-handed) space. */
  map: [number, number, number, number];
  /** Height in metres = low + value * range, where value is the image's 16-bit reading scaled to 0..1. */
  low: number;
  range: number;
  /** Where the map's zero is in the scene. */
  origin: THREE.Vector3;
}
/** Depth (m) over which water goes from clear to its full colour. */
const WATER_FADE = 1.4;

/**
 * Water: dark, smooth, rippled, mirroring the sky. For anything whose Blender
 * material is called Water or Sea.
 *
 * With the map's height image (`depth`), it also knows how deep it is at
 * every pixel: nothing at the waterline, clear and pale over the shallows,
 * its own colour and opacity once it is deep. Without, it meets the land in
 * a hard line. The image holds 16-bit heights split across two channels,
 * which the GPU's texture filter must not blend (half-way between two byte
 * pairs is not half-way between two heights): so it is read unfiltered at
 * the four pixels around the point and blended here after decoding.
 */
export function waterMaterial(ripples: THREE.Texture, open: boolean, depth: WaterDepth | null = null): THREE.Material {
  ripples.wrapS = ripples.wrapT = THREE.RepeatWrapping;
  const base = new THREE.Color(open ? 0x163845 : 0x1c3a36);
  const opacity = open ? 0.94 : 0.86;
  const water = new THREE.MeshStandardNodeMaterial({ color: base, roughness: 0.05, metalness: 0, transparent: true, opacity });
  water.envMapIntensity = 2.4;
  const flow = (scale: number, x: number, z: number) => texture(ripples, positionWorld.xz.mul(scale).add(time.mul(vec2(x, z)))).xy.mul(2).sub(1);
  const tilt = flow(0.11, 0.012, 0.019).add(flow(0.29, -0.021, 0.011)).mul(open ? 0.22 : 0.13);
  water.normalNode = transformNormalToView(normalize(vec3(tilt.x, 1, tilt.y)));
  water.side = THREE.DoubleSide;
  if (depth) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    type N = any;
    const ground = (pixel: N): N => {
      const bytes: N = texture(depth.heights, pixel.add(0.5).div(depth.size)).rg;
      return bytes.r.mul(65280).add(bytes.g.mul(255)).div(65535).mul(depth.range).add(depth.low);
    };
    const own: N = positionWorld.sub(vec3(depth.origin.x, depth.origin.y, depth.origin.z));
    const at: N = vec2(own.x.negate().mul(depth.map[0]).add(depth.map[2]), own.z.mul(depth.map[1]).add(depth.map[3])).mul(depth.size).sub(0.5);
    const cell: N = at.floor();
    const part: N = at.sub(cell);
    const under: N = mix(mix(ground(cell), ground(cell.add(vec2(1, 0))), part.x), mix(ground(cell.add(vec2(0, 1))), ground(cell.add(vec2(1, 1))), part.x), part.y);
    const deepness: N = own.y.sub(under);
    const deep: N = smoothstep(0, WATER_FADE, deepness);
    const colour: N = vec3(base.r, base.g, base.b);
    water.colorNode = mix(colour.mul(2.2).add(vec3(0.02, 0.035, 0.03)), colour, deep);
    water.opacityNode = float(opacity).mul(smoothstep(0, 0.1, deepness)).mul(mix(0.35, 1, deep));
  }
  return water;
}
