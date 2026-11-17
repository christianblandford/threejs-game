import * as THREE from "three/webgpu";
import { float, mix, normalize, positionWorld, smoothstep, texture, time, transformNormalToView, vec2, vec3 } from "three/tsl";

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
