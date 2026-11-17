import * as THREE from "three/webgpu";
import { DynamicLighting } from "three/addons/lighting/DynamicLighting.js";
import { numberOf, params } from "./params";

export type Backend = "webgpu" | "webgl2";

/**
 * three's WebGPU renderer, set up the way everything else here assumes.
 * `?webgl` uses its WebGL 2 backend instead; `?shadows=0` leaves shadows
 * out; `?inspect` adds three's inspector.
 */
export async function createRenderer(canvas: HTMLCanvasElement): Promise<{ renderer: THREE.WebGPURenderer; backend: Backend }> {
  const renderer = new THREE.WebGPURenderer({ canvas, antialias: false, forceWebGL: params.has("webgl") });
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  // Enough that sunlit stone comes close to white (about 225 of 255 at its brightest). Lower, and the
  // whole picture sits in a band of greys with nothing bright in it.
  renderer.toneMappingExposure = numberOf("exposure", 0.78);
  renderer.shadowMap.enabled = params.get("shadows") !== "0";
  renderer.shadowMap.type = THREE.PCFShadowMap;
  // Firelight comes and goes (a fire is walked up to, put out, left behind a
  // door). With three's ordinary lighting every change in the number of lights
  // recompiles every material, a stall of hundreds of milliseconds; with this,
  // point lights are a list the shaders loop over, and its length is free.
  renderer.lighting = new DynamicLighting({ maxPointLights: 16 });
  // `?inspect` opens three's own inspector over the page: frame time by pass,
  // memory, the render targets. Fetched only when asked for.
  if (params.has("inspect")) {
    const { Inspector } = await import("three/addons/inspector/Inspector.js");
    renderer.inspector = new Inspector();
  }
  await renderer.init();
  const backend: Backend = (renderer.backend as unknown as { isWebGPUBackend?: boolean }).isWebGPUBackend ? "webgpu" : "webgl2";
  return { renderer, backend };
}

/** Waits until the GPU has finished everything handed to it. */
export async function gpuDone(renderer: THREE.WebGPURenderer): Promise<void> {
  const inner = renderer.backend as unknown as { device?: { queue: { onSubmittedWorkDone(): Promise<void> } }; gl?: WebGL2RenderingContext };
  if (inner.device) await inner.device.queue.onSubmittedWorkDone();
  else inner.gl?.finish();
}

/**
 * Draws one frame by hand, outside the animation loop.
 *
 * three redraws the scene once per tick of its own animation loop: asked
 * for the picture twice in one tick, it redraws only the final full-screen
 * quad, and a loop of hand-made frames times almost nothing. So the tick is
 * advanced by hand, as the animation loop would do it.
 */
export function drawByHand(renderer: THREE.WebGPURenderer, draw: () => void): void {
  const inner = renderer as unknown as { info: { reset(): void }; _nodes: { nodeFrame: { update(): void } } };
  inner.info.reset();
  inner._nodes.nodeFrame.update();
  draw();
}
