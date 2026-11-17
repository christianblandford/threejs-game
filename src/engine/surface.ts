import * as THREE from "three/webgpu";
import { cameraPosition, cross, distance, float, materialNormal, max, mix, normalWorldGeometry, normalize, positionWorld, smoothstep, texture, transformNormalToView, uniform, uv, vec2, vec3, vertexColor } from "three/tsl";
import type { Blend, MaterialExtras } from "./materials";

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
export function surface(node: THREE.MeshStandardNodeMaterial, standard: THREE.MeshStandardMaterial, extras: MaterialExtras, blend: (Omit<Blend, "layer"> & { layer: THREE.MeshStandardMaterial }) | null): void {
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
