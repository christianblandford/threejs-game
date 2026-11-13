import * as THREE from "three/webgpu";
import { mrt, normalView, output, packNormalToRGB, pass, sample, screenUV, unpackRGBToNormal, velocity } from "three/tsl";
import { CSMShadowNode } from "three/addons/csm/CSMShadowNode.js";
import { SkyMesh } from "three/addons/objects/SkyMesh.js";
import { bloom } from "three/addons/tsl/display/BloomNode.js";
import { ao } from "three/addons/tsl/display/GTAONode.js";
import { sharpen } from "three/addons/tsl/display/SharpenNode.js";
import { traa } from "three/addons/tsl/display/TRAANode.js";
import { loadMap } from "./map";

/**
 * The Babylon scaffold's test town, drawn with three.js, to answer one
 * question with numbers: would the same world at the same quality run faster
 * on three?
 *
 * What is matched to the Babylon game's "high" preset: the same exported map
 * (same chunks at full detail within 80 m, far versions beyond), physically
 * based materials with the same textures, one sun with four 2048 px shadow
 * cascades out to 80 m, sky light from a baked sky, ambient occlusion at
 * half resolution, temporal anti-aliasing, bloom, ACES tone mapping,
 * sharpening, and distance fog.
 *
 * What is not here, and is in the Babylon frame: grass, particles, the
 * characters, the HUD, physics, navigation, and that project's own material
 * extensions (large-scale variation, de-tiling, blended paths, wind, water).
 *
 * `?webgl` uses three's WebGL 2 backend instead of WebGPU; `?post=0` leaves
 * out the post-processing; `?shadows=0` the shadows.
 */

const params = new URLSearchParams(location.search);
const canvas = document.querySelector<HTMLCanvasElement>("#game")!;
const statsBox = document.querySelector<HTMLElement>("#stats")!;

const renderer = new THREE.WebGPURenderer({ canvas, antialias: false, forceWebGL: params.has("webgl") });
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.8;
renderer.shadowMap.enabled = params.get("shadows") !== "0";
renderer.shadowMap.type = THREE.PCFShadowMap;
await renderer.init();
const backend = (renderer.backend as unknown as { isWebGPUBackend?: boolean }).isWebGPUBackend ? "webgpu" : "webgl2";

const scene = new THREE.Scene();
// The game's vertical field of view is 1 radian.
const camera = new THREE.PerspectiveCamera(THREE.MathUtils.radToDeg(1.0), 1, 0.1, 1500);

// --- Sun, sky and sky light --------------------------------------------------
// The game's sun direction (toward the sun), with x negated for three's handedness.
const sunDirection = new THREE.Vector3(-0.45, 0.6, 0.66).normalize();
const sun = new THREE.DirectionalLight(0xfff4e0, 3.4);
sun.position.copy(sunDirection).multiplyScalar(200);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 1;
sun.shadow.camera.far = 600;
sun.shadow.bias = -0.0005;
sun.shadow.normalBias = 0.03;
const csm = new CSMShadowNode(sun, { cascades: 4, maxFar: 80, mode: "practical", lightMargin: 150 });
csm.fade = true;
(sun.shadow as unknown as { shadowNode: unknown }).shadowNode = csm;
scene.add(sun, sun.target);

const sky = new SkyMesh();
sky.scale.setScalar(1400);
sky.turbidity.value = 3;
sky.rayleigh.value = 1.2;
sky.mieCoefficient.value = 0.004;
sky.mieDirectionalG.value = 0.8;
sky.sunPosition.value.copy(sunDirection);
scene.add(sky);
// Ambient light and reflections, baked once from that sky.
{
  const bakeScene = new THREE.Scene();
  const bakeSky = new SkyMesh();
  bakeSky.scale.setScalar(1000);
  bakeSky.turbidity.value = sky.turbidity.value;
  bakeSky.rayleigh.value = sky.rayleigh.value;
  bakeSky.mieCoefficient.value = sky.mieCoefficient.value;
  bakeSky.mieDirectionalG.value = sky.mieDirectionalG.value;
  bakeSky.sunPosition.value.copy(sunDirection);
  bakeScene.add(bakeSky);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(bakeScene, 0, 0.1, 2000).texture;
  scene.environmentIntensity = 0.6;
  pmrem.dispose();
}
scene.fog = new THREE.FogExp2(0x9fb6d2, 0.0022);

// --- The town ----------------------------------------------------------------
// Where the Babylon measurements were taken: in the market square, looking north.
const SQUARE = new THREE.Vector3(0, 0.2, 18);
const map = await loadMap(renderer, "/maps/test_town/map.json", { around: SQUARE, detailDistance: 80, viewDistance: 400 });
scene.add(map.root);
camera.position.set(SQUARE.x, SQUARE.y + 0.6, SQUARE.z);
camera.rotation.set(0, 0, 0); // down -z: north in the game

// --- The frame ---------------------------------------------------------------
const usePost = params.get("post") !== "0";
const pipeline = new THREE.RenderPipeline(renderer);
if (usePost) {
  // Each effect can be left out by name (`?ao=0`, `?traa=0`, `?bloom=0`,
  // `?sharpen=0`), to see what it costs.
  const on = (name: string): boolean => params.get(name) !== "0";
  // One pass over the scene writes colour, and with it whatever the effects in use need.
  const scenePass = pass(scene, camera);
  const targets: Record<string, unknown> = { output };
  if (on("ao")) targets.normal = packNormalToRGB(normalView);
  if (on("traa")) targets.velocity = velocity;
  if (Object.keys(targets).length > 1) scenePass.setMRT(mrt(targets as never));
  const depth = scenePass.getTextureNode("depth");
  let picture: any = scenePass.getTextureNode("output"); // eslint-disable-line @typescript-eslint/no-explicit-any

  if (on("ao")) {
    // Ambient occlusion at half resolution, darkening the picture as the Babylon game's does.
    const normals = sample((uv) => unpackRGBToNormal(scenePass.getTextureNode("normal").sample(uv)));
    scenePass.getTexture("normal").type = THREE.UnsignedByteType;
    const occlusion = ao(depth, normals, camera);
    occlusion.resolutionScale = 0.5;
    occlusion.samples.value = Number(params.get("aoSamples") ?? 8);
    occlusion.radius.value = 1.4;
    occlusion.useTemporalFiltering = on("traa");
    picture = picture.mul(occlusion.getTextureNode().sample(screenUV).r);
  }
  if (on("traa")) picture = traa(picture, depth, scenePass.getTextureNode("velocity"), camera);
  if (on("bloom")) picture = picture.add(bloom(picture, 0.22, 0.6, 1.7));
  if (on("sharpen")) picture = sharpen(picture, 0.3);
  pipeline.outputNode = picture;
} else {
  pipeline.outputNode = pass(scene, camera);
}

/** Sets the canvas to an exact number of pixels, as the Babylon measurements did. */
function setPixels(width: number, height: number): void {
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  // The cascades are cut from the camera's view, so they are recut when it changes shape.
  if ((csm as unknown as { camera: unknown }).camera) csm.updateFrustums();
}
function fit(): void {
  const ratio = Math.min(window.devicePixelRatio, 1.5); // the game's "high" preset caps it there
  setPixels(Math.round(canvas.clientWidth * ratio), Math.round(canvas.clientHeight * ratio));
}
fit();
let fitted = true;
window.addEventListener("resize", () => fitted && fit());

function frame(): void {
  pipeline.render(); // the shadow cascades follow the camera by themselves
}

// --- Looking around ------------------------------------------------------------
const keys = new Set<string>();
let yaw = 0;
let pitch = 0;
window.addEventListener("keydown", (event) => keys.add(event.code));
window.addEventListener("keyup", (event) => keys.delete(event.code));
canvas.addEventListener("click", () => void canvas.requestPointerLock());
window.addEventListener("mousemove", (event) => {
  if (document.pointerLockElement !== canvas) return;
  yaw -= event.movementX * 0.0022;
  pitch = Math.max(-1.4, Math.min(1.4, pitch - event.movementY * 0.0022));
});
function move(dt: number): void {
  camera.rotation.set(pitch, yaw, 0, "YXZ");
  const speed = (keys.has("ShiftLeft") ? 14 : 4) * dt;
  const forward = Number(keys.has("KeyW")) - Number(keys.has("KeyS"));
  const side = Number(keys.has("KeyD")) - Number(keys.has("KeyA"));
  camera.position.x += (-Math.sin(yaw) * forward + Math.cos(yaw) * side) * speed;
  camera.position.z += (-Math.cos(yaw) * forward - Math.sin(yaw) * side) * speed;
}

let last = performance.now();
let smoothed = 16;
let frames = 0;
let running = true;
renderer.setAnimationLoop(() => {
  if (!running) return;
  const now = performance.now();
  const dt = Math.min(0.1, (now - last) / 1000);
  smoothed += (now - last - smoothed) * 0.05;
  last = now;
  move(dt);
  frame();
  if (frames++ % 20 === 0) {
    const info = renderer.info.render as unknown as { drawCalls: number; triangles: number };
    statsBox.textContent = `${Math.round(1000 / smoothed)} fps · ${smoothed.toFixed(1)} ms · ${info.drawCalls} draws · three r${THREE.REVISION} ${backend}`;
  }
});

// --- Measuring -----------------------------------------------------------------
/** Waits until the GPU has finished everything handed to it. */
async function gpuDone(): Promise<void> {
  const inner = renderer.backend as unknown as { device?: { queue: { onSubmittedWorkDone(): Promise<void> } }; gl?: WebGL2RenderingContext };
  if (inner.device) await inner.device.queue.onSubmittedWorkDone();
  else inner.gl?.finish();
}
const median = (values: number[]): number => +[...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]!.toFixed(1);

/**
 * Frame time with the GPU's share included, the way the Babylon project
 * measures it: draw a frame, wait for the GPU, time the whole.
 */
async function measure(count = 120): Promise<{ cpu: number; total: number; draws: number; triangles: number }> {
  running = false;
  // three redraws the scene once per tick of its own animation loop: asked
  // for the picture twice in one tick, it redraws only the final full-screen
  // quad, and a loop of hand-made frames times almost nothing. So the tick
  // is advanced by hand, as the animation loop would do it.
  const inner = renderer as unknown as { info: { reset(): void }; _nodes: { nodeFrame: { update(): void } } };
  const cpu: number[] = [];
  const total: number[] = [];
  for (let i = 0; i < count + 20; i++) {
    const start = performance.now();
    inner.info.reset();
    inner._nodes.nodeFrame.update();
    frame();
    const handed = performance.now();
    await gpuDone();
    if (i >= 20) {
      cpu.push(handed - start);
      total.push(performance.now() - start);
    }
  }
  const info = renderer.info.render as unknown as { drawCalls: number; triangles: number };
  running = true;
  return { cpu: median(cpu), total: median(total), draws: info.drawCalls, triangles: info.triangles };
}

Object.assign(window, {
  bench: {
    renderer,
    scene,
    camera,
    map,
    backend,
    measure,
    frame,
    setPixels(width: number, height: number) {
      fitted = false;
      setPixels(width, height);
    },
    look(x: number, y: number, z: number, newYaw = 0, newPitch = 0) {
      camera.position.set(x, y, z);
      yaw = newYaw;
      pitch = newPitch;
      camera.rotation.set(pitch, yaw, 0, "YXZ");
    },
  },
});
console.info("map", map.stats);
