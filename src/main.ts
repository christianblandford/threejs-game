import * as THREE from "three/webgpu";
import { add, diffuseColor, float, luminance, mrt, normalView, output, packNormalToRGB, pass, rtt, sample, screenUV, smoothstep, unpackRGBToNormal, vec3, vec4, velocity } from "three/tsl";
import { PointerLockControls } from "three/addons/controls/PointerLockControls.js";
import { CSMShadowNode } from "three/addons/csm/CSMShadowNode.js";
import { DynamicLighting } from "three/addons/lighting/DynamicLighting.js";
import { SkyMesh } from "three/addons/objects/SkyMesh.js";
import { bloom } from "three/addons/tsl/display/BloomNode.js";
import { denoise } from "three/addons/tsl/display/DenoiseNode.js";
import { ao } from "three/addons/tsl/display/GTAONode.js";
import { sharpen } from "three/addons/tsl/display/SharpenNode.js";
import { ssgi } from "three/addons/tsl/display/SSGINode.js";
import { taau } from "three/addons/tsl/display/TAAUNode.js";
import { traa } from "three/addons/tsl/display/TRAANode.js";
import { createAudio } from "./audio";
import { LEVEL } from "./config";
import { createConversations } from "./conversations";
import { createEffects } from "./effects";
import { createGate } from "./gate";
import { createHud } from "./hud";
import { Input, PAD } from "./input";
import { createInteract } from "./interact";
import { SHADOW_LAYER, loadMap, type LoadedMap } from "./map";
import { createMenu } from "./menu";
import { createNavigation, navBox, type NavSource } from "./navigation";
import { createNpcs } from "./npcs";
import { perf } from "./perf";
import { createPhysics } from "./physics";
import { createPlayer } from "./player";
import { createResolutionGovernor } from "./resolution";
import { getSettings, onSettingsChanged, QUALITY, type QualityLevel } from "./settings";
import { createSpaces, markerYaw } from "./spaces";
import { createWorldState } from "./worldState";

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
 * Since then it has grown toward that project: grass, wind, water, physics
 * and a player, doors and interiors, a saved world, dialogue, fire and smoke.
 * The README's parity table says what is still missing.
 *
 * `?webgl` uses three's WebGL 2 backend instead of WebGPU; `?post=0` leaves
 * out the post-processing; `?shadows=0` the shadows.
 */

const params = new URLSearchParams(location.search);
const canvas = document.querySelector<HTMLCanvasElement>("#game")!;
const statsBox = document.querySelector<HTMLElement>("#stats")!;

const renderer = new THREE.WebGPURenderer({ canvas, antialias: false, forceWebGL: params.has("webgl") });
renderer.toneMapping = THREE.ACESFilmicToneMapping;
// Enough that sunlit stone comes close to white (about 225 of 255 at its brightest). Lower, and the
// whole picture sits in a band of greys with nothing bright in it.
renderer.toneMappingExposure = Number(params.get("exposure") ?? 0.78);
renderer.shadowMap.enabled = params.get("shadows") !== "0";
renderer.shadowMap.type = THREE.PCFShadowMap;
// Firelight comes and goes (a fire is walked up to, put out, left behind a
// door). With three's ordinary lighting every change in the number of lights
// recompiles every material, a stall of hundreds of milliseconds; with this,
// point lights are a list the shaders loop over, and its length is free.
renderer.lighting = new DynamicLighting({ maxPointLights: 16 });
await renderer.init();
const backend = (renderer.backend as unknown as { isWebGPUBackend?: boolean }).isWebGPUBackend ? "webgpu" : "webgl2";

const scene = new THREE.Scene();
// The game's vertical field of view is 1 radian.
const camera = new THREE.PerspectiveCamera(THREE.MathUtils.radToDeg(1.0), 1, 0.1, 1500);
// The pass that draws the world (the effect chain further down is built on
// it). Made here because the particles need to know how far away the world
// is at each pixel, to hide behind it.
const scenePass = pass(scene, camera);
// One point light that is always there, giving no light: the first one to
// appear would otherwise be the one change that does recompile everything.
scene.add(new THREE.PointLight(0x000000, 0));

// Sound: three's listener rides on the camera. No sound files yet: a few
// effects are made up from a description (audio.ts).
const audio = createAudio(camera);
audio.masterVolume = getSettings().masterVolume;
audio.synth("jump", { duration: 0.16, frequency: 260, frequencyEnd: 520, gain: 0.25 });
audio.synth("land", { duration: 0.14, frequency: 90, frequencyEnd: 50, noise: 0.6, gain: 0.6 });

// --- Sun, sky and sky light --------------------------------------------------
// The game's sun direction (toward the sun), with x negated for three's handedness.
const sunDirection = new THREE.Vector3(-0.45, 0.6, 0.66).normalize();
// Strong against the sky's light: sunlit stone should be several times brighter than stone in shade.
const sun = new THREE.DirectionalLight(0xffedd2, 7.5);
sun.position.copy(sunDirection).multiplyScalar(200);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 1;
sun.shadow.camera.far = 600;
sun.shadow.bias = -0.0005;
sun.shadow.normalBias = 0.03;
// How far the shadow's edge is spread, in shadow-map texels. At 1 the edge
// of a house's shadow is a ruled line a pixel or two wide, however far the
// shadow is thrown; the real sun's is a hand's width after twenty metres.
sun.shadow.radius = Number(params.get("shadowSoft") ?? 3);
const csm = new CSMShadowNode(sun, { cascades: 4, maxFar: 80, mode: "practical", lightMargin: 150 });
csm.fade = true;
(sun.shadow as unknown as { shadowNode: unknown }).shadowNode = csm;
scene.add(sun, sun.target);

const sky = new SkyMesh();
sky.scale.setScalar(1400);
// Little haze, and what there is kept close around the sun: with more, a
// third of the sky toward the sun is a white glare.
sky.turbidity.value = 1.6;
sky.rayleigh.value = 1.3;
sky.mieCoefficient.value = 0.0012;
sky.mieDirectionalG.value = 0.9;
sky.sunPosition.value.copy(sunDirection);
// Fair-weather cloud, drifting.
sky.cloudCoverage.value = 0.42;
sky.cloudDensity.value = 0.55;
sky.cloudElevation.value = 0.45;
// The sky model is physical, and toward the sun it is many times brighter
// than sunlit stone: exposed for the town, a third of the sky in that
// direction is plain white with no blue and no cloud in it. Its brightness
// is eased off as it rises, the way a film stock's shoulder does, so the
// bright side keeps its colour; the dim side is hardly touched. (Only what
// is seen is changed: the light the sky gives the town is baked from another copy.)
{
  const material = sky.material as THREE.NodeMaterial;
  const physical = material.colorNode as any; // eslint-disable-line @typescript-eslint/no-explicit-any
  // Turned down first (`level`), then eased off above `shoulder`: turning it
  // down before easing keeps cloud brighter than sky. Eased alone, both end
  // up pressed against the same ceiling and the sky is a pale blank.
  const level = Number(params.get("skyLevel") ?? 0.3);
  const shoulder = Number(params.get("skyShoulder") ?? 1.5);
  const dimmed = physical.rgb.mul(level);
  material.colorNode = vec4(dimmed.div(luminance(dimmed).div(shoulder).add(1)), 1);
}
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
  // And the ground, lit by the sun, as the other half of what lights the
  // town: a warm light from below. The sky model alone carries on under the
  // horizon as more sky, so everything out of the sun was filled with the
  // same cool light from every side and had no form: the underside of an
  // eave was lit like the top of a wall. (The sky it replaces measures
  // about 5: at 5 only the colour changes, at 1.5 shaded walls are a fifth
  // darker. A little above that, 6, so that house fronts in shade are filled
  // from the bright ground in front of them. `?ground=none` is the sky alone.)
  const bounce = params.get("ground");
  if (bounce !== "none") {
    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(900, 48).rotateX(-Math.PI / 2),
      new THREE.MeshBasicNodeMaterial({ color: new THREE.Color(1, 0.84, 0.64).multiplyScalar(Number(bounce ?? 6)) }),
    );
    ground.position.y = -4;
    bakeScene.add(ground);
  }
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(bakeScene, 0, 0.1, 2000).texture;
  // The sky model is very bright next to the sun light; at more than this, shade is as light as sun.
  scene.environmentIntensity = Number(params.get("ambient") ?? 0.245);
  pmrem.dispose();
}
scene.fog = new THREE.FogExp2(0x8fa9c9, 0.0012);

// --- The town ----------------------------------------------------------------
// Where the Babylon measurements were taken: in the market square, looking north.
const SQUARE = new THREE.Vector3(0, 0.2, 18);
const physics = await createPhysics();
// `?detail=` and `?view=` set how far full detail and anything at all reach (m): small values show the streaming at work.
const reach = { detail: Number(params.get("detail") ?? 80), view: Number(params.get("view") ?? 400) };
// How much grass goes with the quality setting, as it stands when the level loads (`?grass=` overrides it).
const startQuality = params.get("quality") && params.get("quality")! in QUALITY ? (params.get("quality") as QualityLevel) : getSettings().quality;
const map = await loadMap(renderer, LEVEL.map, { around: SQUARE, detailDistance: reach.detail, viewDistance: reach.view, grass: Number(params.get("grass") ?? QUALITY[startQuality].grass), materials: LEVEL.materials, physics });
scene.add(map.root);
// Start where the map says: its `spawn` marker, facing the way it faces.
// (Markers are in the Babylon game's left-handed space: x is negated, and a
// heading of `yaw` there is `pi - yaw` here.)
const spawnMarker = map.manifest.markers.find((marker) => marker.name === "spawn");
const spawn = map.marker("spawn") ?? SQUARE.clone();
map.update(spawn);
await map.settled(); // the ground must be there before anything stands on it
/** The water's surface where the player is: asked of the place they are in (set once places exist, below). */
let waterHere = (position: THREE.Vector3): number | null => map.waterAt(position.x, position.z);
const player = await createPlayer(scene, camera, physics, spawn.clone().add(new THREE.Vector3(0, 0.3, 0)), spawnMarker ? markerYaw(spawnMarker) : 0, { water: (position) => waterHere(position) });
player.onJump = () => audio.play("jump", { playbackRate: 0.95 + Math.random() * 0.1 });
// (What a landing and a splash look like is added below, once there are effects.)

// --- The game: what is remembered, what can be used, where one can go ---------
// What the player has done, saved in the browser as it changes. `?newgame` starts it empty.
const state = createWorldState();
if (params.has("newgame")) state.reset();
const hud = createHud();
const interact = createInteract();
const conversations = createConversations({ state, interact, hud, player });
const effects = createEffects({ sceneDepth: scenePass.getTextureNode("depth"), lights: scene });
// A landing raises dust, and is heard, in proportion to how hard it was.
player.onLand = (impactSpeed) => {
  audio.play("land", { volume: Math.min(1, impactSpeed / 8) });
  effects.burst("dust", player.position, { scale: Math.min(1.6, impactSpeed / 4) });
};
// Going into water, or climbing out, throws some of it about.
player.onSwimChanged = ({ swimming, speed }) => {
  audio.play("land", { volume: Math.min(1, 0.4 + speed / 8), playbackRate: swimming ? 0.5 : 0.8 });
  effects.burst("splash", player.position.clone().add(new THREE.Vector3(0, 0.42, 0)), { scale: swimming ? Math.min(1.8, 0.7 + speed / 5) : 0.6 });
};

// Where characters can walk: worked out from everything solid in every map
// that is loaded, and again (only where it changed) whenever a chunk comes
// or goes, or something like the gate shuts a way through.
const navigation = await createNavigation();
const walked: LoadedMap[] = [];
let blockers: NavSource[] = [];
function rewalk(): void {
  const meshes = [...blockers];
  const obstacles = [];
  for (const each of walked) {
    const solid = each.collisionGeometry();
    for (const piece of solid.meshes) meshes.push(piece);
    for (const obstacle of solid.obstacles) obstacles.push(obstacle);
  }
  void navigation.rebuild({ meshes, obstacles });
}

// The level's own light, which each place's is a share of (a room has less sky, no haze, and eyes adjusted to it).
const daylight = { sun: sun.intensity, ambient: scene.environmentIntensity, fog: (scene.fog as THREE.FogExp2).density, exposure: renderer.toneMappingExposure, shadowDistance: csm.maxFar };
const spaces = createSpaces({
  home: { id: LEVEL.id, map },
  interact,
  find: (id) => LEVEL.interiors.find((interior) => interior.id === id),
  async load(space, origin) {
    // An interior is small: all of it in detail, no grass.
    const interior = await loadMap(renderer, space.map, { around: origin, detailDistance: 200, viewDistance: 400, grass: 0, physics, origin });
    scene.add(interior.root);
    return interior;
  },
  attach(_id, attached) {
    conversations.followMap(attached);
    effects.followMap(attached);
    walked.push(attached);
    attached.onDetailChanged = rewalk;
    rewalk();
  },
  light(environment) {
    sun.intensity = daylight.sun * environment.sun;
    scene.environmentIntensity = daylight.ambient * environment.ambient;
    (scene.fog as THREE.FogExp2).density = daylight.fog * environment.fog;
    renderer.toneMappingExposure = daylight.exposure * environment.exposure;
    const reach = environment.shadowDistance ?? daylight.shadowDistance;
    if (csm.maxFar !== reach) {
      csm.maxFar = reach;
      if ((csm as unknown as { camera: unknown }).camera) csm.updateFrustums();
    }
  },
  place: (position, toYaw) => player.teleport(position, toYaw),
  whereabouts: () => ({ position: player.position, yaw: player.yaw }),
});
const gate = createGate({
  map,
  physics,
  interact,
  hud,
  state: state.scope(LEVEL.id),
  feet: player.position,
  block(box) {
    blockers = box ? [navBox(box.centre, box.half, box.rotation)] : [];
    rewalk();
  },
  moved: (what) => audio.play("land", what === "lever" ? { volume: 0.5, playbackRate: 2 } : { volume: 0.9, playbackRate: 0.45 }),
});
waterHere = (position) => spaces.map(spaces.current)?.waterAt(position.x, position.z) ?? null;
// A few foxes with business of their own, in the square.
const npcs = await createNpcs({ parent: map.root, navigation, physics, around: map.marker("market_square") ?? SQUARE, player });
// N shows where they can walk.
let navShown = false;
window.addEventListener("keydown", (event) => {
  if (event.code === "KeyN") navigation.showDebug((navShown = !navShown) ? scene : null);
});
// Walking into a named part of the town says so.
const PLACES: Record<string, string> = { town_gate: "The town gate", market_square: "Market square", wall_walk: "The wall walk", hall_terrace: "The hall", moat: "The moat" };
for (const [zone, name] of Object.entries(PLACES)) interact.onEnter(zone, () => hud.toast(name));
// Fires can be put out and lit again, and stay as they were left.
const fires = state.scope(LEVEL.id).scope("fire");
for (const fire of interact.all("interact", "campfire")) {
  if (!fires.get(`${fire.marker.name}.out`, false)) continue;
  effects.setPlaced(fire.marker.name, false);
  fire.prompt = "Light the fire";
}
interact.on("interact", (used) => {
  if (used.name === "well") {
    hud.toast("The water is cold and clear.");
    audio.play("land", { volume: 0.4, playbackRate: 1.6 });
    effects.burst("dust", used.position);
  }
  if (used.name !== "campfire") return;
  const relight = fires.get(`${used.marker.name}.out`, false);
  fires.set(`${used.marker.name}.out`, relight ? undefined : true);
  effects.setPlaced(used.marker.name, relight);
  if (relight) effects.burst("sparks", used.position);
  used.prompt = relight ? "Put out the fire" : "Light the fire";
});

// Where the player is, kept in the saved world: relative to the place's own
// zero, since where an interior sits in the scene depends on what was loaded
// before it. `?continue` picks the game up there.
const whereabouts = state.scope("player");
function saveWhereabouts(): void {
  if (spaces.travelling) return;
  const at = player.position.clone().sub(spaces.origin(spaces.current));
  whereabouts.set("space", spaces.current);
  whereabouts.set("x", Math.round(at.x * 100) / 100);
  whereabouts.set("y", Math.round(at.y * 100) / 100);
  whereabouts.set("z", Math.round(at.z * 100) / 100);
  whereabouts.set("yaw", Math.round(player.yaw * 100) / 100);
}
spaces.onTravelled = () => {
  saveWhereabouts();
  audio.play("land", { volume: 0.5, playbackRate: 0.7 });
};
const savedGame = (): boolean => whereabouts.peek("space") !== undefined;
/** Puts the player where the saved game left them. */
async function resumeSaved(): Promise<void> {
  if (!savedGame()) return;
  const at = { position: new THREE.Vector3(whereabouts.get("x", 0), whereabouts.get("y", 0), whereabouts.get("z", 0)), yaw: whereabouts.get("yaw", 0) };
  const space = whereabouts.get("space", LEVEL.id);
  if (space === LEVEL.id) {
    map.update(at.position);
    await map.settled();
    player.teleport(at.position, at.yaw);
  } else await spaces.travel(space, at).catch((error) => console.warn("Could not pick the saved game up where it was:", error));
}

// --- Menus -----------------------------------------------------------------------
// The title screen comes first, unless something says how to start: a URL
// switch, or the menu itself across a reload ("New game" and "Restart level"
// reload the page: what the world remembers decides how it is built). In
// development the game starts playing, as the Babylon project does; `?menu`
// shows the title screen.
const START_KEY = "three-start";
const asked = sessionStorage.getItem(START_KEY);
sessionStorage.removeItem(START_KEY);
const starting = params.has("continue") ? "continue" : asked ?? (import.meta.env.DEV && !params.has("menu") ? "play" : null);
if (starting === "continue") await resumeSaved();
const menu = createMenu({ title: LEVEL.title, canContinue: savedGame, initialState: starting ? "playing" : "menu" });
const reloadTo = (start: "play" | "continue"): void => {
  state.flush();
  sessionStorage.setItem(START_KEY, start);
  // Without the switches that would start it some other way.
  const url = new URL(location.href);
  for (const name of ["newgame", "continue", "menu"]) url.searchParams.delete(name);
  location.replace(url);
};
menu.onPlay = () => menu.set("playing");
menu.onContinue = () => void resumeSaved().then(() => menu.set("playing"));
menu.onNewGame = () => {
  state.reset();
  reloadTo("play");
};
menu.onRestart = () => {
  // The level as it was at its start: what was done in it is forgotten (the
  // gate, the fires) along with where the player was. Without that, lowering
  // the gate and restarting leaves the player on the road outside a town
  // whose only lever is inside. What people remember of conversations stays.
  state.forget(`${LEVEL.id}/`);
  state.forget("player/");
  reloadTo("play");
};
// Where the player is, is remembered only while they are playing: the title screen is not a place.
setInterval(() => menu.state === "playing" && saveWhereabouts(), 2000);

// --- The frame ---------------------------------------------------------------
const usePost = params.get("post") !== "0";
const pipeline = new THREE.RenderPipeline(renderer);
/** Effects made for the picture as it is now, to be freed when it is built again. */
let effectNodes: { dispose?(): void }[] = [];
/**
 * Builds the picture for a level of quality (settings.ts says what each
 * means). Called at the start and again when the setting changes: the chain
 * of effects is put together anew and three compiles it on the next frame.
 */
function compose(level: QualityLevel): void {
  const preset = QUALITY[level];
  for (const node of effectNodes) node.dispose?.();
  effectNodes = [];
  const made = <T>(node: T): T => {
    effectNodes.push(node as { dispose?(): void });
    return node;
  };
  if (!usePost) {
    const particles = pass(effects.layer, camera);
    pipeline.outputNode = vec4(scenePass.rgb.mul(particles.a.oneMinus()).add(particles.rgb), 1);
    pipeline.needsUpdate = true;
    return;
  }
  // Each effect can be forced on or off by name whatever the quality
  // (`?gi=0`, `?ao=1`, `?traa=0`, `?bloom=0`), to see what it costs or adds.
  const on = (name: string, otherwise = true): boolean => (params.has(name) ? params.get(name) !== "0" : otherwise);
  const useGi = on("gi", preset.shading === "bounced");
  const useAo = on("ao", preset.shading !== "none");
  // Every pixel is drawn, and steadied by temporal anti-aliasing, which is
  // also what averages away the noise of the bounced light below.
  // `?scale=0.75` draws fewer pixels and has three's temporal upscaler bring
  // them up to size instead. It was the default for an hour: it saves only a
  // few milliseconds here, softens the picture, and does not average that
  // noise well, which shows as grain crawling over everything in shade.
  const scale = Math.min(1, Number(params.get("scale") ?? 1));
  // One pass over the scene writes colour, and with it whatever the effects in use need.
  if (scale < 1 && on("traa")) scenePass.setResolutionScale(scale);
  // Always the same set, whatever the quality uses of it. A pass keeps the
  // buffers it was first given: asked later to write fewer, three.js looks
  // for the missing ones while compiling every material and throws.
  scenePass.setMRT(mrt({ output, diffuseColor, normal: packNormalToRGB(normalView), velocity } as never));
  const depth = scenePass.getTextureNode("depth");
  const colour = scenePass.getTextureNode("output");
  let picture: any = colour; // eslint-disable-line @typescript-eslint/no-explicit-any
  const normals = (): any => {
    scenePass.getTexture("normal").type = THREE.UnsignedByteType;
    return sample((uv) => unpackRGBToNormal(scenePass.getTextureNode("normal").sample(uv)));
  };

  if (useGi) {
    // Light bounced off what is on screen, and the shade in creases, from
    // one effect: a sunlit wall lights the wall opposite, a red awning
    // tints the plaster under it. This is what the Babylon version of the
    // town lacks most.
    scenePass.getTexture("diffuseColor").type = THREE.UnsignedByteType;
    // What it gathers light from is the picture with its brightest points
    // held down to about sunlit white. A lamp is a few pixels many times
    // brighter than that: the handful of random samples that happen to land
    // on it light their pixels like sparks, and the wall beside every lamp
    // is peppered with orange. The lamp still glows (bloom) and still lights
    // the wall, as much as a sunlit surface that size would.
    const bounce = made(ssgi(vec4(colour.rgb.min(Number(params.get("giSourceLimit") ?? 2)), 1), depth, normals(), camera));
    // The effect samples at random; what settles the result is the smoothing
    // further down and temporal anti-aliasing. With both, one slice of eight
    // steps is enough (settings.ts and the README have the measurements).
    bounce.sliceCount.value = Number(params.get("giSlices") ?? (preset.slices || 2));
    bounce.stepCount.value = Number(params.get("giSteps") ?? (preset.steps || 6));
    bounce.radius.value = 8;
    bounce.giIntensity.value = 14;
    bounce.aoIntensity.value = Number(params.get("broadStrength") ?? 1);
    bounce.thickness.value = 0.6;
    bounce.useTemporalFiltering = on("traa") && on("giTemporal");
    // At well under half the canvas each way: it is the dearest effect in the frame, and what it adds is soft.
    bounce.resolutionScale = Number(params.get("giScale") ?? (preset.bounceScale || 0.5));
    // What comes out is grainy: a few random samples per pixel. Temporal
    // anti-aliasing averages most of it away on textured surfaces, but on
    // anything of one flat colour (an awning, plaster) what is left shows as
    // dark mottling that crawls. So both results are first smoothed across
    // neighbouring pixels that lie on the same surface (same depth, same
    // facing). `?denoise=0` shows it without.
    // The smoothing is done at half the canvas each way and stretched back
    // up, not at full size. Written into the picture's own shader it ran for
    // every pixel of the canvas, twice (shade, and bounced light), and was
    // the dearest thing in the frame: 8.5 ms of 26 at 2.25 million pixels,
    // more than the bounced light it was cleaning. What it smooths was only
    // ever worked out at half size. `?smoothScale=1` is the old way.
    const smoothScale = Number(params.get("smoothScale") ?? 0.5);
    const smooth = (node: any, type: THREE.TextureDataType = THREE.HalfFloatType): any => { // eslint-disable-line @typescript-eslint/no-explicit-any
      if (!on("denoise")) return node;
      const cleaned = made(denoise(node, depth, normals(), camera));
      cleaned.radius.value = Number(params.get("denoiseRadius") ?? 8);
      return made(rtt(cleaned, null, null, { resolutionScale: smoothScale, type } as never));
    };
    // That effect reaches eight metres, to carry light across a street, and
    // what it gives as shade is as broad: a general dimming, with nothing at
    // the foot of a wall or under an eave. A second, short-reach occlusion
    // puts the dark where two surfaces meet.
    // The two are multiplied before they are smoothed, not after: smoothing
    // is the dear part (a full-size pass), the short-reach occlusion needs it
    // as much as the broad one (raised to a power, its grain flickers ten
    // times worse than the rest of the picture), and one pass serves both.
    const broad: any = bounce.getAONode(); // eslint-disable-line @typescript-eslint/no-explicit-any
    let occlusion: any = broad; // eslint-disable-line @typescript-eslint/no-explicit-any
    let debug: any = null; // eslint-disable-line @typescript-eslint/no-explicit-any
    if (on("contact")) {
      const near = made(ao(depth, normals(), camera));
      near.resolutionScale = 0.5;
      near.samples.value = 8;
      near.radius.value = Number(params.get("contactRadius") ?? 1.2);
      // As it comes it is faint (hardly anything is hidden from a point by what is within a metre of it): `scale` is the power it is raised to.
      near.scale.value = Number(params.get("contactStrength") ?? 2);
      near.useTemporalFiltering = on("traa");
      const contact: any = near.getTextureNode(); // eslint-disable-line @typescript-eslint/no-explicit-any
      if (params.get("show") === "contact") debug = contact.sample(screenUV).r;
      // (Drawn into a texture of its own first: the smoothing wants a texture to read, not a sum.)
      occlusion = made(rtt(broad.mul(contact.sample(screenUV).r), null, null, { resolutionScale: smoothScale, type: THREE.UnsignedByteType } as never));
    }
    const shade = smooth(occlusion, THREE.UnsignedByteType).r;
    if (params.get("show") === "broad") debug = smooth(broad, THREE.UnsignedByteType).r;
    // Capped: one sample that happens to land on a lamp or the sun's glint
    // would otherwise light its pixel like a spark.
    const bounced = smooth(bounce.getGINode()).rgb.min(2.5);
    picture = vec4(add(colour.rgb.mul(shade), scenePass.getTextureNode("diffuseColor").rgb.mul(bounced)), colour.a);
    // `?show=broad|contact|bounce` puts one of the effect's parts on screen by itself, to see what it is doing.
    if (params.get("show") === "bounce") debug = bounced;
    if (debug) picture = vec4(vec3(debug), 1);
  } else if (useAo) {
    const occlusion = made(ao(depth, normals(), camera));
    occlusion.resolutionScale = 0.5;
    occlusion.samples.value = 8;
    occlusion.radius.value = 1.4;
    occlusion.scale.value = 2; // as it comes it is faint
    occlusion.useTemporalFiltering = on("traa");
    picture = picture.mul(occlusion.getTextureNode().sample(screenUV).r);
  }
  if (on("traa")) {
    const steady = made((scale < 1 ? taau : traa)(picture, depth, scenePass.getTextureNode("velocity"), camera));
    if (!on("subpixel")) (steady as unknown as { useSubpixelCorrection: boolean }).useSubpixelCorrection = false;
    picture = steady;
    // Temporal anti-aliasing steadies the picture and softens it: measured
    // on a still, it takes away more than half of the fine detail (cobbles,
    // stonework, grass). Contrast-adaptive sharpening puts most of it back.
    // Its `denoise` option holds back where the picture is noisy, which is
    // what stops it magnifying the grain of the bounced light: plain
    // sharpening did exactly that here once and was taken out.
    // `?sharpen=0` shows the picture without; `?sharpness=` is 0 (most) to 2 (none).
    if (on("sharpen")) picture = made(sharpen(picture, Number(params.get("sharpness") ?? 0.2), true));
  }
  // Fire and smoke, drawn by themselves and laid over the picture (see
  // effects.ts): after the anti-aliasing, which would smear them, and before
  // the glow, which a flame should have.
  const particles = pass(effects.layer, camera);
  picture = vec4(picture.rgb.mul(particles.a.oneMinus()).add(particles.rgb), 1);
  if (on("bloom", preset.bloom)) {
    // Only what is far brighter than sunlit stone glows (lamps, glints on
    // water, the sun's own patch of sky), and not by much. With a low
    // threshold the whole sky and every sunlit wall bleed into a haze.
    // What glows is also capped. The sun's disc is thousands of times
    // brighter than a lamp: uncapped, even a faint glow from it veils the
    // whole picture in white whenever the sun is in view.
    const glow = made(bloom(vec4(picture.rgb.min(Number(params.get("bloomCap") ?? 4.5)), 1), Number(params.get("bloomStrength") ?? 0.07), 0.35, Number(params.get("bloomThreshold") ?? 3.2)));
    glow.setResolutionScale(0.25); // a soft glow needs few pixels
    picture = picture.add(glow);
  }
  // A little darker toward the corners, as a lens is.
  const fromCentre = screenUV.sub(0.5).length();
  picture = picture.mul(smoothstep(0.95, 0.35, fromCentre).mul(0.28).add(0.72));
  pipeline.outputNode = picture;
  pipeline.needsUpdate = true;
}
/** `?quality=` wins over the saved setting. */
const quality = (): QualityLevel => (params.get("quality") && params.get("quality")! in QUALITY ? (params.get("quality") as QualityLevel) : getSettings().quality);
compose(quality());

/** Sets the canvas to an exact number of pixels, as the Babylon measurements did. */
function setPixels(width: number, height: number): void {
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  // The cascades are cut from the camera's view, so they are recut when it changes shape.
  if ((csm as unknown as { camera: unknown }).camera) csm.updateFrustums();
}
// Fewer pixels when a view is too heavy for sixty frames a second (resolution.ts). `?autoscale=0` holds it off.
const governor = createResolutionGovernor();
governor.enabled = params.get("autoscale") !== "0" && getSettings().autoResolution;
function fit(): void {
  // A Retina screen has four times the pixels of an ordinary one and the eye
  // cannot count them: the picture is built from several frames and sharpened.
  const full = Math.min(window.devicePixelRatio, Number(params.get("ratio") ?? QUALITY[quality()].pixelRatio));
  // Never under 0.7 of a CSS pixel each way: below that the picture is plainly soft.
  const ratio = Math.max(full * governor.scale, Math.min(full, 0.7));
  setPixels(Math.round(canvas.clientWidth * ratio), Math.round(canvas.clientHeight * ratio));
}
fit();
let fitted = true;
window.addEventListener("resize", () => fitted && fit());

function frame(): void {
  // The cameras that draw the shadow maps also see the chunks' shadow-only
  // meshes (map.ts). The cascades' lights are made on the first frame, so
  // this is said every frame: four bit-sets.
  for (const cascade of csm.lights as THREE.DirectionalLight[]) cascade.shadow.camera.layers.enable(SHADOW_LAYER);
  pipeline.render(); // the shadow cascades follow the camera by themselves
}

// --- Looking around ------------------------------------------------------------
// Keyboard and gamepad, as actions (input.ts).
const input = new Input();
/** How fast the gamepad's right stick turns the view (radians a second at full tilt). */
const STICK_LOOK_SPEED = 2.6;
/** The prompt on screen, to change it only when it changes. */
let offered: string | null = null;
let yaw = 0;
let pitch = 0;
// The mouse turns the view through three's own pointer-lock control. It is
// given a stand-in to turn, not the camera: the player's camera is placed by
// player.ts, which wants to know how far the mouse turned this frame. So the
// stand-in is read and set straight again every frame (`mouseTurn`).
const lookRig = new THREE.PerspectiveCamera();
const mouse = new PointerLockControls(lookRig, canvas);
mouse.pointerSpeed = getSettings().lookSensitivity;
const rigTurn = new THREE.Euler(0, 0, 0, "YXZ");
/** How far the mouse has turned the view since last asked: radians left, and up. */
function mouseTurn(): { yaw: number; pitch: number } {
  rigTurn.setFromQuaternion(lookRig.quaternion, "YXZ");
  lookRig.quaternion.identity();
  return { yaw: rigTurn.y, pitch: rigTurn.x * (getSettings().invertY ? -1 : 1) };
}
// (Refused where the page is embedded in another: the game then plays without mouse look.)
const captureMouse = (): void => void Promise.resolve(canvas.requestPointerLock()).catch(() => undefined);
canvas.addEventListener("click", () => menu.state === "playing" && captureMouse());
// Escape lets the mouse go, and the browser keeps that key press to itself: losing the mouse is the pause.
mouse.addEventListener("unlock", () => menu.pause());
window.addEventListener("keydown", (event) => {
  if (event.repeat) return;
  // The weather, and something to try the explosion on: where the Babylon project has a debug panel.
  if (event.code === "KeyR" && menu.state === "playing") effects.raining = !effects.raining;
  else if (event.code === "KeyB" && menu.state === "playing") {
    const ahead = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize().multiplyScalar(6).add(player.position);
    effects.burst("explosion", ahead);
    audio.play("land", { volume: 1, playbackRate: 0.35 });
  }
});
menu.onStateChanged = (now) => {
  if (now === "playing") captureMouse();
  else if (mouse.isLocked) mouse.unlock();
  if (now !== "playing") hud.prompt((offered = null));
  input.releaseAll();
};
onSettingsChanged((settings, changed) => {
  audio.masterVolume = settings.masterVolume;
  mouse.pointerSpeed = settings.lookSensitivity;
  if (changed.autoResolution !== undefined) governor.enabled = params.get("autoscale") !== "0" && settings.autoResolution;
  if (changed.quality !== undefined) {
    compose(quality());
    governor.reset(); // what fitted the old setting says nothing about the new one
    if (fitted) fit();
  }
});
/** Walking about as the fox, or flying the camera freely (F switches; the measuring hooks fly). */
let flying = params.has("fly");
window.addEventListener("keydown", (event) => {
  if (event.code !== "KeyF") return;
  flying = !flying;
  if (flying) {
    // Take off from where the camera is, looking the way it looks.
    const facing = camera.getWorldDirection(new THREE.Vector3());
    yaw = Math.atan2(-facing.x, -facing.z);
    pitch = Math.asin(THREE.MathUtils.clamp(facing.y, -1, 1));
  }
});
canvas.addEventListener("wheel", (event) => menu.state === "playing" && player.zoom(Math.sign(event.deltaY)), { passive: true });
function move(dt: number): void {
  camera.rotation.set(pitch, yaw, 0, "YXZ");
  const speed = (input.isDown("sprint") ? 14 : 4) * dt;
  const forward = input.move.y;
  const side = input.move.x;
  camera.position.x += (-Math.sin(yaw) * forward + Math.cos(yaw) * side) * speed;
  camera.position.z += (-Math.cos(yaw) * forward - Math.sin(yaw) * side) * speed;
}

let last = performance.now();
let smoothed = 16;
let frames = 0;
let running = true;
// three's own frame timer: it leaves out the time a hidden page was not drawing.
const timer = new THREE.Timer();
timer.connect(document);
renderer.setAnimationLoop(() => {
  if (!running) return;
  const now = performance.now();
  smoothed += (now - last - smoothed) * 0.05;
  // Not while a test has fixed the canvas's size.
  if (fitted && governor.sample(now - last)) fit();
  last = now;
  timer.update();
  // Behind a menu the world stands still: nothing is given any time.
  const live = menu.state === "playing";
  const dt = live ? Math.min(0.1, timer.getDelta()) : 0;
  input.update();
  if (input.wasPressed("pause")) menu.togglePause();
  // The view is turned by the mouse, and by the gamepad's right stick (a rate, so by the time that has passed).
  const turned = mouseTurn();
  const stick = STICK_LOOK_SPEED * getSettings().lookSensitivity * dt;
  turned.yaw -= input.lookStick.x * stick;
  turned.pitch -= input.lookStick.y * stick * (getSettings().invertY ? -1 : 1);
  if (!live) {
    // The keys and the pad are the menu's.
    menu.steer({
      move: Number(input.wasPadPressed(PAD.down)) - Number(input.wasPadPressed(PAD.up)),
      adjust: Number(input.wasPadPressed(PAD.right)) - Number(input.wasPadPressed(PAD.left)),
      accept: input.wasPadPressed(PAD.accept),
      back: input.wasPadPressed(PAD.back),
    });
  } else if (flying) {
    yaw += turned.yaw;
    pitch = Math.max(-1.4, Math.min(1.4, pitch + turned.pitch));
    move(dt);
    if (offered !== null) hud.prompt((offered = null));
  } else {
    player.look(-turned.yaw, -turned.pitch);
    // Space answers in a conversation; it is a jump only when nobody is talking.
    const busy = conversations.talking || spaces.travelling;
    const jumping = input.wasPressed("jump") && !busy;
    if (input.wasPressed("toggleView")) player.setViewMode(player.viewMode === "first" ? "third" : "first");
    interact.update(player.position, camera, spaces.current, input.wasPressed("interact"));
    // Asked every frame, not on a change of focus: using a thing may change what it offers.
    const offer = interact.focus?.prompt ?? null;
    if (offer !== offered) hud.prompt((offered = offer));
    conversations.update(dt, {
      next: input.wasPressed("interact") || input.wasPressed("jump"),
      digit: input.digitPressed(),
      step: Number(input.wasPadPressed(PAD.down)) - Number(input.wasPadPressed(PAD.up)),
    });
    gate?.update(dt);
    player.frozen = busy;
    player.update(dt, {
      move: input.move,
      run: input.isDown("sprint"),
      jump: jumping,
    });
    physics.step(dt);
  }
  input.endFrame();
  spaces.update(camera.position); // streams the place the player is in around the camera
  navigation.update(dt, camera.position);
  npcs.update(dt);
  effects.update(dt, camera.position);
  sky.position.copy(camera.position); // the sky is always around whoever looks at it
  frame();
  if (frames++ % 20 === 0) {
    const info = renderer.info.render as unknown as { drawCalls: number; triangles: number };
    statsBox.textContent = `${Math.round(1000 / smoothed)} fps · ${smoothed.toFixed(1)} ms · ${info.drawCalls} draws · three r${THREE.REVISION} ${backend}${governor.scale < 1 ? ` · ${Math.round(governor.scale * 100)}% size` : ""}${flying ? " · flying" : ""}`;
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
 * What a frame costs when frames are drawn one after another without
 * waiting: 20 frames are handed to the GPU and timed to the end, then 60,
 * and the difference is divided by the 40 between. `measure` below waits
 * for the GPU after every frame, and in some browsers that wait is itself
 * 50 ms or more; here it is paid once per run and cancels out.
 * `cpu` is the script's share of a frame.
 */
async function pace(): Promise<{ frame: number; cpu: number }> {
  running = false;
  const inner = renderer as unknown as { info: { reset(): void }; _nodes: { nodeFrame: { update(): void } } };
  const burst = async (count: number): Promise<[number, number]> => {
    const start = performance.now();
    for (let i = 0; i < count; i++) {
      inner.info.reset();
      inner._nodes.nodeFrame.update();
      frame();
    }
    const handed = performance.now() - start;
    await gpuDone();
    return [performance.now() - start, handed / count];
  };
  await burst(10);
  const [short] = await burst(20);
  const [long, cpu] = await burst(60);
  running = true;
  return { frame: +((long - short) / 40).toFixed(1), cpu: +cpu.toFixed(1) };
}

/**
 * Sets one of the `?name=value` switches as if it were in the address and
 * builds the picture again, so two settings can be timed back to back in
 * one page. (Two loads of the page differ by several milliseconds on
 * their own.) `null` takes the switch away. Only for what `compose` reads.
 */
function tune(name: string, value: string | null): void {
  if (value === null) params.delete(name);
  else params.set(name, value);
  compose(quality());
}

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

/**
 * How much the picture changes from frame to frame with the camera still:
 * draws `count` frames and, for each pixel, takes the spread between its
 * darkest and lightest value. A steady picture gives nearly nothing; one
 * that shimmers or crawls gives a lot. `show` lays the spread over the
 * page (white = changing) until the next call with `show` false.
 */
async function flicker(count = 16, show = false): Promise<{ mean: number; busy: number }> {
  running = false;
  const inner = renderer as unknown as { info: { reset(): void }; _nodes: { nodeFrame: { update(): void } } };
  const width = Math.round(canvas.width / 2);
  const height = Math.round(canvas.height / 2);
  const copy = document.createElement("canvas");
  copy.width = width;
  copy.height = height;
  const context = copy.getContext("2d", { willReadFrequently: true })!;
  const low = new Uint8Array(width * height).fill(255);
  const high = new Uint8Array(width * height);
  for (let i = 0; i < count + 30; i++) {
    inner.info.reset();
    inner._nodes.nodeFrame.update();
    frame();
    if (i >= 30) {
      context.drawImage(canvas, 0, 0, width, height);
      const pixels = context.getImageData(0, 0, width, height).data;
      for (let p = 0, at = 0; p < low.length; p++, at += 4) {
        const light = (pixels[at]! * 3 + pixels[at + 1]! * 6 + pixels[at + 2]!) / 10;
        if (light < low[p]!) low[p] = light;
        if (light > high[p]!) high[p] = light;
      }
    }
    await gpuDone();
  }
  let sum = 0;
  let busy = 0;
  const image = context.createImageData(width, height);
  for (let p = 0; p < low.length; p++) {
    const spread = high[p]! - low[p]!;
    sum += spread;
    if (spread > 20) busy++;
    const shown = Math.min(255, spread * 5);
    image.data[p * 4] = image.data[p * 4 + 1] = image.data[p * 4 + 2] = shown;
    image.data[p * 4 + 3] = 255;
  }
  document.querySelector("#flicker")?.remove();
  if (show) {
    context.putImageData(image, 0, 0);
    copy.id = "flicker";
    copy.style.cssText = "position:fixed;inset:0;width:100%;height:100%;z-index:3";
    document.body.appendChild(copy);
  }
  running = true;
  return { mean: +(sum / low.length).toFixed(2), busy: +((100 * busy) / low.length).toFixed(2) };
}

/**
 * Flies the camera through `points` at `speed` m/s, a sixtieth of a second
 * of travel per frame, and times every frame with the GPU's share and
 * whatever arrived and was set up in between. What matters is the worst
 * frames: that is the stutter a player would feel as the map streams.
 */
async function run(points: [number, number, number][], speed = 12): Promise<Record<string, unknown>> {
  running = false;
  flying = true;
  // Always the same number of pixels, whatever size the window happens to be: runs are only comparable so.
  fitted = false;
  setPixels(1280, 800);
  const inner = renderer as unknown as { info: { reset(): void }; _nodes: { nodeFrame: { update(): void } } };
  perf.clear();
  perf.recording = true;
  const times: number[] = [];
  /** For each frame: streaming, drawing (CPU), waiting for the GPU, and what arrived in between. */
  const parts: [number, number, number, number][] = [];
  const from = new THREE.Vector3();
  const to = new THREE.Vector3();
  for (let leg = 0; leg + 1 < points.length; leg++) {
    from.fromArray(points[leg]!);
    to.fromArray(points[leg + 1]!);
    const steps = Math.max(1, Math.round((from.distanceTo(to) / speed) * 60));
    for (let step = 0; step < steps; step++) {
      const start = performance.now();
      camera.position.lerpVectors(from, to, step / steps);
      camera.lookAt(to.x, camera.position.y - 1, to.z);
      spaces.update(camera.position);
      effects.update(1 / 60, camera.position);
      navigation.update(1 / 60, camera.position);
      npcs.update(1 / 60);
      const streamed = performance.now();
      inner.info.reset();
      inner._nodes.nodeFrame.update();
      frame();
      const drawn = performance.now();
      await gpuDone();
      const finished = performance.now();
      await new Promise((resolve) => setTimeout(resolve)); // lets what has arrived be dealt with, as between real frames
      const end = performance.now();
      times.push(end - start);
      parts.push([streamed - start, drawn - streamed, finished - drawn, end - finished]);
    }
  }
  perf.recording = false;
  running = true;
  const sorted = [...times].sort((a, b) => a - b);
  const at = (share: number): number => +sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * share))]!.toFixed(1);
  return {
    frames: times.length,
    median: at(0.5),
    p95: at(0.95),
    p99: at(0.99),
    worst: +sorted[sorted.length - 1]!.toFixed(1),
    over33: times.filter((ms) => ms > 33.3).length,
    over50: times.filter((ms) => ms > 50).length,
    work: perf.totals(),
    slowest: perf.slowest(6).map((entry) => `${entry.label} ${entry.ms.toFixed(1)}`),
    // The eight worst frames: total = streaming + drawing + GPU + arrivals.
    // Twenty frames at a time: the mean of drawing and of the GPU, to see when it is slow and for how long.
    series: Array.from({ length: Math.ceil(parts.length / 20) }, (_, bucket) => {
      const slice = parts.slice(bucket * 20, bucket * 20 + 20);
      const mean = (column: number): number => Math.round(slice.reduce((sum, part) => sum + part[column]!, 0) / slice.length);
      return `${mean(1)}/${mean(2)}`;
    }).join(" "),
    worstFrames: times
      .map((ms, i) => ({ ms, i }))
      .sort((a, b) => b.ms - a.ms)
      .slice(0, 8)
      .map(({ ms, i }) => `#${i} ${ms.toFixed(0)} = ${parts[i]!.map((part) => part.toFixed(0)).join(" + ")}`),
  };
}

Object.assign(window, {
  bench: {
    run,
    perf,
    flicker,
    renderer,
    scene,
    camera,
    map,
    backend,
    measure,
    pace,
    tune,
    governor,
    frame,
    setPixels(width: number, height: number) {
      fitted = false;
      setPixels(width, height);
    },
    player,
    physics,
    menu,
    input,
    audio,
    compose,
    spaces,
    state,
    interact,
    conversations,
    effects,
    navigation,
    npcs,
    async look(x: number, y: number, z: number, newYaw = 0, newPitch = 0) {
      flying = true;
      camera.position.set(x, y, z);
      map.update(camera.position);
      await map.settled();
      yaw = newYaw;
      pitch = newPitch;
      camera.rotation.set(pitch, yaw, 0, "YXZ");
    },
  },
});
console.info("map", map.stats);
