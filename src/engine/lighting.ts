import * as THREE from "three/webgpu";
import { luminance, vec4 } from "three/tsl";
import { CSMShadowNode } from "three/addons/csm/CSMShadowNode.js";
import { SkyMesh } from "three/addons/objects/SkyMesh.js";
import { SHADOW_LAYER } from "./map";
import { numberOf, params } from "./params";
import type { Environment } from "./spaces";

/**
 * The sun, its shadows, the sky, and the sky's light baked into the scene's
 * environment. One of these per game: a level, and each place in it, asks
 * for a share of it (`apply`).
 */
export interface Lighting {
  readonly sun: THREE.DirectionalLight;
  readonly shadows: CSMShadowNode;
  readonly sky: SkyMesh;
  /** Sets the light for a place, as shares of the level's own daylight: a room has less sky, no haze, and eyes adjusted to it. */
  apply(environment: Environment): void;
  /** Once a frame, before drawing: the sky stays around whoever looks at it, and the shadow cameras are told what they may see. */
  update(eye: THREE.Vector3): void;
  /** The shadow cascades are cut from the camera's view, so they are cut again when it changes shape. */
  recut(): void;
}

/** The game's sun direction (toward the sun), with x negated for three's handedness. */
const SUN_DIRECTION = new THREE.Vector3(-0.45, 0.6, 0.66).normalize();

export function createLighting(renderer: THREE.WebGPURenderer, scene: THREE.Scene): Lighting {
  // Strong against the sky's light: sunlit stone should be several times brighter than stone in shade.
  const sun = new THREE.DirectionalLight(0xffedd2, 7.5);
  sun.position.copy(SUN_DIRECTION).multiplyScalar(200);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 600;
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.03;
  // How far the shadow's edge is spread, in shadow-map texels. At 1 the edge
  // of a house's shadow is a ruled line a pixel or two wide, however far the
  // shadow is thrown; the real sun's is a hand's width after twenty metres.
  sun.shadow.radius = numberOf("shadowSoft", 3);
  const shadows = new CSMShadowNode(sun, { cascades: 4, maxFar: 80, mode: "practical", lightMargin: 150 });
  shadows.fade = true;
  (sun.shadow as unknown as { shadowNode: unknown }).shadowNode = shadows;
  scene.add(sun, sun.target);

  const sky = createSky();
  scene.add(sky);
  bakeSkyLight(renderer, scene, sky);
  scene.fog = new THREE.FogExp2(0x8fa9c9, 0.0012);

  const fog = scene.fog as THREE.FogExp2;
  // The level's own light, which each place's is a share of.
  const daylight = { sun: sun.intensity, ambient: scene.environmentIntensity, fog: fog.density, exposure: renderer.toneMappingExposure, shadowDistance: shadows.maxFar };
  const recut = (): void => {
    // (Nothing to cut until the cascades have been set up, on the first frame.)
    if ((shadows as unknown as { camera: unknown }).camera) shadows.updateFrustums();
  };

  return {
    sun,
    shadows,
    sky,
    apply(environment) {
      sun.intensity = daylight.sun * environment.sun;
      scene.environmentIntensity = daylight.ambient * environment.ambient;
      fog.density = daylight.fog * environment.fog;
      renderer.toneMappingExposure = daylight.exposure * environment.exposure;
      const reach = environment.shadowDistance ?? daylight.shadowDistance;
      if (shadows.maxFar !== reach) {
        shadows.maxFar = reach;
        recut();
      }
    },
    update(eye) {
      sky.position.copy(eye);
      // The cameras that draw the shadow maps also see the chunks' shadow-only
      // meshes (map/shadowProxy.ts). The cascades' lights are made on the first frame, so
      // this is said every frame: four bit-sets.
      for (const cascade of shadows.lights as THREE.DirectionalLight[]) cascade.shadow.camera.layers.enable(SHADOW_LAYER);
    },
    recut,
  };
}

function createSky(): SkyMesh {
  const sky = new SkyMesh();
  sky.scale.setScalar(1400);
  // Little haze, and what there is kept close around the sun: with more, a
  // third of the sky toward the sun is a white glare.
  sky.turbidity.value = 1.6;
  sky.rayleigh.value = 1.3;
  sky.mieCoefficient.value = 0.0012;
  sky.mieDirectionalG.value = 0.9;
  sky.sunPosition.value.copy(SUN_DIRECTION);
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
  const material = sky.material as THREE.NodeMaterial;
  const physical = material.colorNode as any; // eslint-disable-line @typescript-eslint/no-explicit-any
  // Turned down first (`level`), then eased off above `shoulder`: turning it
  // down before easing keeps cloud brighter than sky. Eased alone, both end
  // up pressed against the same ceiling and the sky is a pale blank.
  const level = numberOf("skyLevel", 0.3);
  const shoulder = numberOf("skyShoulder", 1.5);
  const dimmed = physical.rgb.mul(level);
  material.colorNode = vec4(dimmed.div(luminance(dimmed).div(shoulder).add(1)), 1);
  return sky;
}

/** Ambient light and reflections, baked once from the sky as it is drawn (without its clouds or its easing). */
function bakeSkyLight(renderer: THREE.WebGPURenderer, scene: THREE.Scene, sky: SkyMesh): void {
  const bakeScene = new THREE.Scene();
  const bakeSky = new SkyMesh();
  bakeSky.scale.setScalar(1000);
  bakeSky.turbidity.value = sky.turbidity.value;
  bakeSky.rayleigh.value = sky.rayleigh.value;
  bakeSky.mieCoefficient.value = sky.mieCoefficient.value;
  bakeSky.mieDirectionalG.value = sky.mieDirectionalG.value;
  bakeSky.sunPosition.value.copy(SUN_DIRECTION);
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
  scene.environmentIntensity = numberOf("ambient", 0.245);
  pmrem.dispose();
}
