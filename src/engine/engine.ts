import * as THREE from "three/webgpu";
import { createAudio, type GameAudio } from "./audio";
import { createEffects, type Effects } from "./effects";
import { Input } from "./input";
import { createInteract, type Interact } from "./interact";
import { createLighting, type Lighting } from "./lighting";
import { createLoop, type Loop } from "./loop";
import { createMouseLook, type MouseLook } from "./mouseLook";
import { createNavigation, type Navigation } from "./navigation";
import { activeQuality, numberOf, params } from "./params";
import { createPhysics, type Physics } from "./physics";
import { createPicture, type Picture } from "./picture";
import { createRandom, type Random } from "./random";
import { createRenderer, type Backend } from "./renderer";
import { getSettings, onSettingsChanged } from "./settings";
import { createViewport, type Viewport } from "./viewport";
import { createWalkable, type Walkable } from "./walkable";
import { createWorldState, type WorldState } from "./worldState";

/**
 * Everything a game is built on, made once and handed to whatever needs it:
 * the renderer and its picture, light, physics, navigation, particles, the
 * things that can be used, input, sound, and what the world remembers.
 *
 * Nothing in here knows which game it is. A game (src/game/) loads a level
 * into `scene`, puts a player in it and decides what happens; this is what
 * it does not have to write again.
 */
export interface Engine {
  readonly canvas: HTMLCanvasElement;
  readonly renderer: THREE.WebGPURenderer;
  readonly backend: Backend;
  readonly scene: THREE.Scene;
  /** The player's view. Whoever controls the player places it. */
  readonly camera: THREE.PerspectiveCamera;
  readonly lighting: Lighting;
  readonly picture: Picture;
  readonly viewport: Viewport;
  readonly loop: Loop;
  readonly physics: Physics;
  readonly navigation: Navigation;
  readonly walkable: Walkable;
  readonly effects: Effects;
  readonly interact: Interact;
  readonly input: Input;
  readonly mouse: MouseLook;
  readonly audio: GameAudio;
  /** What the player has done, saved in the browser as it changes. `?slot=` keeps it apart from the real saved game (a test's own). */
  readonly state: WorldState;
  /** The game's dice: seeded (`?seed=`), so that a run can be played again. */
  readonly random: Random;
  /** Draws a frame. */
  draw(): void;
}

export async function createEngine(canvas: HTMLCanvasElement): Promise<Engine> {
  const { renderer, backend } = await createRenderer(canvas);
  const scene = new THREE.Scene();
  // The game's vertical field of view is 1 radian.
  const camera = new THREE.PerspectiveCamera(THREE.MathUtils.radToDeg(1.0), 1, 0.1, 1500);
  const picture = createPicture(renderer, scene, camera);
  // One point light that is always there, giving no light: the first one to
  // appear would otherwise be the one change that does recompile everything.
  scene.add(new THREE.PointLight(0x000000, 0));

  // Sound: three's listener rides on the camera.
  const audio = createAudio(camera);
  audio.masterVolume = getSettings().masterVolume;
  const lighting = createLighting(renderer, scene);
  const physics = await createPhysics();
  const state = createWorldState(params.get("slot") ?? "save");
  const random = createRandom(numberOf("seed", 1));
  // `?newgame` starts it empty.
  if (params.has("newgame")) state.reset();
  const interact = createInteract();
  const effects = createEffects({ sceneDepth: picture.sceneDepth, lights: scene });
  picture.overlay = effects.layer;
  const navigation = await createNavigation();
  navigation.reseed(random.seed);
  const walkable = createWalkable(navigation);
  const input = new Input();
  const mouse = createMouseLook(canvas);

  picture.compose(activeQuality());
  const viewport = createViewport(renderer, canvas, camera, lighting.recut);
  const loop = createLoop(renderer);

  onSettingsChanged((settings, changed) => {
    audio.masterVolume = settings.masterVolume;
    mouse.sensitivity = settings.lookSensitivity;
    if (changed.autoResolution !== undefined) viewport.refresh();
    if (changed.quality !== undefined) {
      picture.compose(activeQuality());
      viewport.governor.reset(); // what fitted the old setting says nothing about the new one
      if (!viewport.pinned) viewport.fit();
    }
  });

  return {
    canvas,
    renderer,
    backend,
    scene,
    camera,
    lighting,
    picture,
    viewport,
    loop,
    physics,
    navigation,
    walkable,
    effects,
    interact,
    input,
    mouse,
    audio,
    state,
    random,
    draw() {
      lighting.update(camera.position);
      picture.render(); // the shadow cascades follow the camera by themselves
    },
  };
}
