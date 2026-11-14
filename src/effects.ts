import * as THREE from "three/webgpu";
import { atan, cameraFar, cameraNear, cameraPosition, clamp, cos, exp, float, hash, instanceIndex, mix, modelViewMatrix, mx_noise_float, perspectiveDepthToViewZ, positionView, screenUV, sin, smoothstep, uniform, userData, uv, vec2, vec3, vec4 } from "three/tsl";
import type { LoadedMap } from "./map";
import { WIND } from "./materials";
import { markerPosition } from "./spaces";

/**
 * Fire, smoke and firelight, placed in Blender: any Empty with a custom
 * property `fx` (`campfire`: flames, sparks, smoke and a flickering light;
 * `brazier`: the same without smoke, for a fire indoors; `smoke`: a chimney)
 * and optionally `fx_scale` (1 is a fire about a metre tall).
 *
 * **Particles** are not simulated. Each is a square that always faces the
 * camera, and where it is, how big and what colour is worked out on the GPU
 * from the time and the particle's number alone: a flame is born at the
 * fire's foot, rises, shrinks and reddens, and is born again. Nothing is
 * kept per particle, so a fire costs one small draw per kind of particle
 * and no work on the CPU.
 *
 * They are drawn in **their own pass**, laid over the finished picture
 * (`layer`). In the main pass they would write into the buffers the effects
 * read (surface direction, movement), which hold one value per pixel and
 * cannot blend: every particle would punch a square hole in them. What the
 * main pass would have given them, being hidden behind walls, is done by
 * hand: each pixel of a particle compares its distance with the scene's and
 * fades out as it nears a surface, which also softens where smoke meets a
 * roof.
 *
 * **Lights** are ordinary point lights, kept under the map they belong to
 * (so they go dark with it). main.ts sets the renderer up so that point
 * lights coming and going never recompile a material (`DynamicLighting`).
 *
 * **Bursts** (`burst("dust", at)`: also `splash`, `sparks`, `explosion`)
 * are the same kind of particle, born all at once: each is a function of
 * the time since its burst began, thrown out, pulled down, faded. A burst
 * takes a few sprites from a small pool and gives them back when it is over.
 *
 * **Rain** (`raining = true`) is a box of streaks around the camera. Each
 * drop falls through the box and comes in again at the top, and the box is
 * wrapped around wherever the camera is, so there is always rain where one
 * looks and none is ever made or thrown away.
 *
 * Everything moves by the time handed to `update`, so pausing stops it.
 */

export type BurstName = "dust" | "splash" | "sparks" | "explosion";

export interface Effects {
  /** The particles: a scene of their own, drawn over the picture after the main pass. */
  readonly layer: THREE.Scene;
  /** Starts the effects placed in a map. They run while the map is shown and the camera is near. */
  followMap(map: LoadedMap): void;
  /** Switches an effect placed in a map off or back on, by its Empty's name. */
  setPlaced(markerName: string, on: boolean): void;
  /** A one-off effect at a point. `scale` 1 is a sensible size next to the player. */
  burst(name: BurstName, at: THREE.Vector3, options?: { scale?: number }): void;
  /** True while rain is falling around the camera. */
  raining: boolean;
  update(dt: number, eye: THREE.Vector3): void;
}

/** Effects run while the camera is within this distance (m), with a little slack before stopping. */
const DISTANCE = 110;
const SLACK = 20;
/**
 * Particles are unlit and the picture is HDR under a bright sun: a flame has
 * to be several times brighter than sunlit stone to read as fire, and
 * smoke about as bright as a sunlit grey wall.
 */
const FLAME_BRIGHTNESS = 3.4;
const SPARK_BRIGHTNESS = 9;
const SMOKE_BRIGHTNESS = 1.25;

interface Placed {
  name: string;
  map: LoadedMap;
  position: THREE.Vector3;
  /** Everything of this effect in the particle layer. */
  group: THREE.Group;
  parts: THREE.Sprite[];
  light: THREE.PointLight | null;
  strength: number;
  /** 0 to 1: fades in and out so nothing pops. */
  level: number;
  on: boolean;
  near: boolean;
  phase: number;
}

// TSL's types do not follow a value through this much arithmetic; the shapes are checked by running it.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Node = any;

export function createEffects(options: { sceneDepth: unknown; lights: THREE.Object3D }): Effects {
  const layer = new THREE.Scene();
  layer.name = "effects";
  const clock = uniform(0);
  const placed: Placed[] = [];

  // --- What a particle is ------------------------------------------------------
  // Three numbers of its own, the same every frame, from the particle's index
  // and its fire's (so no two fires move in step).
  const seed: Node = userData("seed", "float");
  const level: Node = userData("level", "float");
  const id: Node = float(instanceIndex).add(seed);
  const a: Node = hash(id);
  const b: Node = hash(id.add(17.3));
  const c: Node = hash(id.add(41.7));
  /** 0 as it is born, 1 as it is gone, then born again: every particle on its own beat. */
  const lifeAt = (perSecond: Node): Node => clock.mul(perSecond).add(a.mul(7.13)).fract();
  /** A round soft dot. */
  const dot: Node = smoothstep(0.05, 0.5, uv().sub(0.5).length()).oneMinus();
  /** 1 in the open, falling to 0 as the particle nears whatever is behind it; 0 when it is behind. */
  // The scene's depth is read at this pixel of the screen, said outright. A
  // pass's own ready-made node for this (`getViewZNode()`) reads the depth
  // with the material's default texture coordinates, which on a sprite are
  // the sprite's own: every particle then compares itself with the whole
  // screen shrunk onto it, and draws as a little picture of the room.
  const sceneViewZ: Node = perspectiveDepthToViewZ((options.sceneDepth as Node).sample(screenUV), cameraNear, cameraFar);
  const unhidden = (softness: number): Node => clamp(positionView.z.sub(sceneViewZ).div(softness), 0, 1);

  function material(name: string): THREE.SpriteNodeMaterial {
    const made = new THREE.SpriteNodeMaterial({ transparent: true, depthWrite: false, depthTest: false, fog: false });
    made.name = name;
    // Colour already multiplied by how much of it there is: light is added,
    // and what is behind is covered by `alpha` (none, for a flame).
    made.blending = THREE.CustomBlending;
    made.blendSrc = THREE.OneFactor;
    made.blendDst = THREE.OneMinusSrcAlphaFactor;
    made.blendSrcAlpha = THREE.OneFactor;
    made.blendDstAlpha = THREE.OneMinusSrcAlphaFactor;
    return made;
  }

  // Flames: born across the fire's foot, drawn together as they rise, from yellow-white to red.
  const flames = material("flames");
  {
    const life: Node = lifeAt(mix(1.1, 1.8, c));
    const narrowing = life.mul(0.75).oneMinus();
    const lick = sin(clock.mul(5).add(a.mul(40))).mul(0.05).mul(life);
    flames.positionNode = vec3(a.sub(0.5).mul(0.36).mul(narrowing).add(lick), life.pow(0.8).mul(0.8).add(0.08), b.sub(0.5).mul(0.36).mul(narrowing));
    // Taller than wide: tongues, not balls.
    flames.scaleNode = vec2(mix(0.3, 0.07, life), mix(0.5, 0.16, life));
    const colour: Node = mix(vec3(1, 0.4, 0.07), vec3(0.85, 0.07, 0.01), life.pow(0.6)).mul(FLAME_BRIGHTNESS);
    const amount: Node = dot.mul(dot).mul(smoothstep(0, 0.1, life)).mul(life.oneMinus()).mul(0.42).mul(level).mul(unhidden(0.25));
    // Mostly light added to what is behind, with a little covering it: against a sunlit wall, light alone is a pale ghost.
    flames.outputNode = vec4(colour.mul(amount), amount.mul(0.7));
  }

  // Sparks: a few bright points that leave the fire and wander up.
  const sparks = material("sparks");
  {
    const life: Node = lifeAt(mix(0.45, 0.8, c));
    const wander = sin(clock.mul(2.3).add(b.mul(30))).mul(0.18).mul(life);
    sparks.positionNode = vec3(a.sub(0.5).mul(0.3).add(wander), life.mul(mix(1.2, 2.2, b)).add(0.3), b.sub(0.5).mul(0.3).add(wander.mul(c.sub(0.5))));
    sparks.scaleNode = float(0.03);
    const amount: Node = dot.mul(life.oneMinus()).mul(smoothstep(0, 0.05, life)).mul(level).mul(unhidden(0.1));
    sparks.outputNode = vec4(vec3(1, 0.5, 0.14).mul(SPARK_BRIGHTNESS).mul(amount), 0);
  }

  // Smoke: slow grey puffs that swell as they climb and lean with the wind.
  const smoke = material("smoke");
  {
    const life: Node = lifeAt(mix(0.16, 0.22, c));
    const lean = life.mul(life).mul(2.2);
    const wobble = sin(clock.mul(0.7).add(a.mul(20))).mul(0.25).mul(life);
    smoke.positionNode = vec3(
      a.sub(0.5).mul(0.3).add(wobble).add(lean.mul(WIND.direction.x)),
      life.mul(5).add(0.25),
      b.sub(0.5).mul(0.3).add(lean.mul(WIND.direction.y)),
    );
    smoke.scaleNode = mix(0.4, 2.1, life.pow(0.7));
    smoke.rotationNode = a.mul(6.28).add(clock.mul(b.sub(0.5)).mul(0.5));
    // Ragged, not round: each puff has its own pattern of thick and thin.
    const ragged: Node = mx_noise_float(uv().mul(2.6).add(a.mul(31)), 1, 0).mul(0.5).add(0.6);
    const amount: Node = dot.mul(ragged).mul(smoothstep(0, 0.12, life)).mul(life.oneMinus().pow(1.4)).mul(0.6).mul(level).mul(unhidden(0.6));
    // Darker when thick and young, paler as it thins.
    const colour: Node = mix(vec3(0.46, 0.45, 0.44), vec3(0.78, 0.79, 0.82), life).mul(SMOKE_BRIGHTNESS);
    smoke.outputNode = vec4(colour.mul(amount), amount);
  }

  // --- Bursts --------------------------------------------------------------------
  // Seconds since the burst this sprite belongs to began.
  const born: Node = userData("born", "float");
  const age: Node = (clock as Node).sub(born);
  const d: Node = hash(id.add(73.9));
  /** A direction within `spread` radians of straight up, each particle its own. */
  const cone = (spread: number): Node => {
    const tilt: Node = a.sqrt().mul(spread);
    const round: Node = b.mul(6.2832);
    const lean: Node = sin(tilt);
    const rise: Node = cos(tilt);
    return vec3(lean.mul(cos(round)), rise, lean.mul(sin(round)));
  };
  /** 0 as a particle is born with its burst, 1 as it is gone (and beyond: it stays gone). */
  const lifeOver = (shortest: number, longest: number): Node => age.div(mix(shortest, longest, c));
  const alive = (life: Node): Node => life.lessThan(1).select(1, 0);

  // Dust: a low puff that spreads, slows and thins. For landings and impacts.
  const dust = material("dust");
  {
    const life: Node = lifeOver(0.45, 0.95);
    // Thrown outward and a little up, slowed by the air: distance covered tails off.
    const throwTo: Node = vec3(a.sub(0.5).mul(2), mix(0.25, 0.7, d), b.sub(0.5).mul(2)).mul(mix(0.25, 0.9, c));
    const travelled: Node = exp(age.mul(-2.2)).oneMinus().div(2.2);
    dust.positionNode = vec3(a.sub(0.5).mul(0.24), 0.03, b.sub(0.5).mul(0.24)).add(throwTo.mul(travelled)).add(vec3(0, age.mul(age).mul(0.12), 0));
    dust.scaleNode = mix(0.14, 0.3, d).mul(mix(0.5, 1.9, life.min(1)));
    const amount: Node = dot.mul(life.oneMinus().max(0)).mul(0.38).mul(alive(life)).mul(unhidden(0.2));
    dust.outputNode = vec4(vec3(0.75, 0.68, 0.55).mul(1.6).mul(amount), amount);
  }

  // Splash: water thrown up and falling back.
  const splash = material("splash");
  {
    const life: Node = lifeOver(0.35, 0.8);
    const velocity: Node = cone(0.98).mul(mix(1.2, 3.2, d));
    splash.positionNode = vec3(a.sub(0.5).mul(0.4), 0, b.sub(0.5).mul(0.4)).add(velocity.mul(age)).add(vec3(0, age.mul(age).mul(-4.5), 0));
    splash.scaleNode = mix(0.03, 0.09, c);
    const amount: Node = dot.mul(life.oneMinus().max(0)).mul(0.75).mul(alive(life)).mul(unhidden(0.08));
    splash.outputNode = vec4(vec3(0.8, 0.88, 0.95).mul(2).mul(amount), amount);
  }

  // Sparks: hot points that arc and fall, each drawn out along the way it is going.
  const hot = material("burst sparks");
  {
    const life: Node = lifeOver(0.3, 0.9);
    const launch: Node = cone(0.714).mul(mix(1.8, 5, d));
    hot.positionNode = launch.mul(age).add(vec3(0, age.mul(age).mul(-4.9), 0));
    const velocity: Node = launch.add(vec3(0, age.mul(-9.81), 0));
    // On screen: which way it is going (to turn the sprite) and how fast (to draw it out).
    const across: Node = modelViewMatrix.mul(vec4(velocity, 0)).xy;
    hot.rotationNode = atan(across.y, across.x).sub(1.5708);
    hot.scaleNode = vec2(mix(0.025, 0.05, c), mix(0.025, 0.05, c).add(across.length().mul(0.025)));
    const amount: Node = dot.mul(alive(life)).mul(unhidden(0.08));
    const colour: Node = mix(mix(vec3(6, 3.2, 0.8), vec3(5, 1.5, 0.2), life.mul(2).min(1)), vec3(1, 0.1, 0), life.mul(2).sub(1).clamp(0, 1));
    hot.outputNode = vec4(colour.mul(amount).mul(life.oneMinus().max(0).sqrt()), 0);
  }

  // An explosion's ball of fire: bright, swelling fast, gone in half a second.
  const fireball = material("fireball");
  {
    const life: Node = lifeOver(0.3, 0.6);
    const round: Node = b.mul(6.2832);
    const up: Node = a.mul(2).sub(1);
    const out: Node = vec3(up.mul(up).oneMinus().sqrt().mul(cos(round)), up.abs(), up.mul(up).oneMinus().sqrt().mul(sin(round)));
    fireball.positionNode = out.mul(mix(0.6, 1.5, d)).mul(exp(age.mul(-5)).oneMinus()).add(vec3(0, 0.3, 0));
    fireball.scaleNode = mix(0.5, 1.5, life.min(1).sqrt());
    const amount: Node = dot.mul(dot).mul(life.oneMinus().max(0)).mul(0.6).mul(alive(life)).mul(unhidden(0.4));
    const colour: Node = mix(vec3(1, 0.6, 0.22), vec3(0.9, 0.13, 0.02), life.min(1).sqrt()).mul(5);
    fireball.outputNode = vec4(colour.mul(amount), amount.mul(0.5));
  }
  // And the smoke it leaves, rolling up and away.
  const blast = material("blast smoke");
  {
    const life: Node = lifeOver(1.2, 2.2);
    const round: Node = b.mul(6.2832);
    const out: Node = vec3(a.sqrt().mul(cos(round)), d.mul(0.6), a.sqrt().mul(sin(round)));
    blast.positionNode = out.mul(1.4).mul(exp(age.mul(-3)).oneMinus()).add(vec3(0, age.mul(0.7).add(0.4), 0));
    blast.scaleNode = mix(0.8, 2.4, life.min(1).pow(0.6));
    blast.rotationNode = a.mul(6.28).add(age.mul(b.sub(0.5)));
    const ragged: Node = mx_noise_float(uv().mul(2.6).add(a.mul(31)), 1, 0).mul(0.5).add(0.6);
    // Not there in the first instant: the fire is seen first, the smoke as it clears.
    // Thick: the picture's bright end is squeezed on its way to the screen, and
    // thin smoke over sunlit stone darkens it by a few per cent, which is nothing.
    const amount: Node = dot.mul(ragged).mul(smoothstep(0.02, 0.2, life)).mul(life.oneMinus().max(0)).mul(1.9).min(0.9).mul(alive(life)).mul(unhidden(0.5));
    blast.outputNode = vec4(mix(vec3(0.07, 0.065, 0.06), vec3(0.4, 0.4, 0.42), life.min(1)).mul(SMOKE_BRIGHTNESS).mul(amount), amount);
  }

  /** What each burst is made of: a material, how many particles, how long until the last is gone (s). */
  const RECIPES: Record<BurstName, { of: THREE.SpriteNodeMaterial; count: number; lasts: number }[]> = {
    dust: [{ of: dust, count: 14, lasts: 1 }],
    splash: [{ of: splash, count: 36, lasts: 0.85 }],
    sparks: [{ of: hot, count: 40, lasts: 0.95 }],
    explosion: [
      { of: blast, count: 14, lasts: 2.3 },
      { of: fireball, count: 22, lasts: 0.65 },
      { of: hot, count: 40, lasts: 0.95 },
    ],
  };
  /** Sprites that have been used for a burst, by material, to use again: a new one costs the renderer a first draw. */
  const idle = new Map<THREE.SpriteNodeMaterial, THREE.Sprite[]>();
  const busy: { sprite: THREE.Sprite; until: number }[] = [];
  // An explosion lights what is around it for an instant. Two lights, always
  // there and dark between flashes: enough for two at once.
  const flashes = [0, 1].map(() => {
    const light = new THREE.PointLight(new THREE.Color(1, 0.7, 0.35), 0, 30, 2);
    light.visible = false;
    options.lights.add(light);
    return { light, born: -10, strength: 0 };
  });

  // --- Rain ------------------------------------------------------------------------
  const RAIN_BOX = new THREE.Vector3(36, 22, 36);
  const rainLevel = uniform(0);
  const rain = material("rain");
  {
    const box: Node = vec3(RAIN_BOX.x, RAIN_BOX.y, RAIN_BOX.z);
    const fallen: Node = (clock as Node).mul(mix(10, 12, c));
    // Where the drop would be in a world filled with boxes of rain; then the box the camera is in.
    const free: Node = vec3(a.mul(RAIN_BOX.x), b.mul(RAIN_BOX.y).sub(fallen), d.mul(RAIN_BOX.z)).add((vec3(WIND.direction.x, 0, WIND.direction.y) as Node).mul((clock as Node).mul(1.5)));
    const fromCamera: Node = free.sub(cameraPosition).add(box.mul(0.5));
    const wrapped: Node = fromCamera.sub(box.mul(fromCamera.div(box).floor()));
    rain.positionNode = wrapped.sub(box.mul(0.5)).add(cameraPosition);
    rain.scaleNode = vec2(0.02, 0.6);
    // Thinning toward the edge of the box, so drops do not pop in and out at a wall of rain.
    const edge: Node = smoothstep(RAIN_BOX.x * 0.5, RAIN_BOX.x * 0.3, wrapped.sub(box.mul(0.5)).xz.length());
    const streak: Node = smoothstep(0.5, 0.1, uv().x.sub(0.5).abs()).mul(smoothstep(0.5, 0.2, uv().y.sub(0.5).abs()));
    const amount: Node = streak.mul(edge).mul(0.5).mul(rainLevel).mul(unhidden(0.3));
    rain.outputNode = vec4(vec3(0.72, 0.78, 0.9).mul(1.6).mul(amount), amount.mul(0.6));
  }
  const drops = new THREE.Sprite(rain);
  drops.count = 6000;
  drops.frustumCulled = false;
  drops.renderOrder = 3;
  drops.visible = false;
  drops.userData.seed = 0;
  layer.add(drops);
  let raining = false;

  function particles(of: THREE.SpriteNodeMaterial, count: number, order: number): THREE.Sprite {
    const sprite = new THREE.Sprite(of);
    sprite.count = count;
    sprite.frustumCulled = false; // where its particles are is decided on the GPU
    sprite.renderOrder = order;
    return sprite;
  }

  function place(map: LoadedMap, name: string, kind: string, position: THREE.Vector3, scale: number): void {
    const group = new THREE.Group();
    group.name = `fx:${name}`;
    group.position.copy(position);
    group.scale.setScalar(scale);
    group.visible = false;
    const parts: THREE.Sprite[] = [];
    const fire = kind === "campfire" || kind === "brazier";
    if (kind === "campfire" || kind === "smoke") {
      const column = particles(smoke, 36, 0);
      if (fire) column.position.y = 0.6; // above the flames
      parts.push(column);
    }
    if (fire) parts.push(particles(flames, 34, 1), particles(sparks, 9, 2));
    // Never below zero: the particles' random numbers are made from whole numbers counted up from this.
    const phase = Math.abs(position.x * 12.9 + position.y * 78.2 + position.z * 37.7) % 100;
    for (const part of parts) {
      part.userData.seed = phase;
      part.userData.level = 0;
      group.add(part);
    }
    layer.add(group);

    let light: THREE.PointLight | null = null;
    if (fire) {
      light = new THREE.PointLight(new THREE.Color(1, 0.5, 0.18), 0, 14 * scale, 2);
      light.name = `firelight:${name}`;
      light.position.copy(position).y += 0.6 * scale;
      light.visible = false;
      map.root.add(light);
    }
    placed.push({ name, map, position, group, parts, light, strength: 16 * scale * scale, level: 0, on: true, near: false, phase });
  }

  let frame = 0;
  return {
    layer,
    followMap(map) {
      for (const marker of map.manifest.markers) {
        const kind = marker.props.fx;
        if (kind !== "campfire" && kind !== "brazier" && kind !== "smoke") {
          if (typeof kind === "string") console.warn(`"${marker.name}" asks for the effect "${kind}", which this game does not have.`);
          continue;
        }
        place(map, marker.name, kind, markerPosition(marker), typeof marker.props.fx_scale === "number" ? marker.props.fx_scale : 1);
      }
    },
    setPlaced(markerName, on) {
      for (const effect of placed) if (effect.name === markerName) effect.on = on;
    },
    burst(name, at, burstOptions = {}) {
      const scale = burstOptions.scale ?? 1;
      for (const { of, count, lasts } of RECIPES[name]) {
        let sprite = idle.get(of)?.pop();
        if (!sprite) {
          sprite = particles(of, count, 2);
          layer.add(sprite);
        }
        sprite.position.copy(at);
        sprite.scale.setScalar(scale);
        sprite.userData.born = clock.value;
        sprite.userData.seed = Math.random() * 90;
        sprite.visible = true;
        busy.push({ sprite, until: clock.value + lasts });
      }
      if (name === "explosion") {
        // The light that has been dark longest.
        const flash = flashes.reduce((oldest, each) => (each.born < oldest.born ? each : oldest));
        flash.light.position.copy(at).y += 0.5 * scale;
        flash.light.distance = 30 * scale;
        flash.born = clock.value;
        flash.strength = 400 * scale * scale;
        flash.light.visible = true;
      }
    },
    get raining() {
      return raining;
    },
    set raining(on: boolean) {
      raining = on;
    },
    update(dt, eye) {
      clock.value += dt;
      // Bursts that are over give their sprites back.
      for (let i = busy.length - 1; i >= 0; i--) {
        const { sprite, until } = busy[i]!;
        if (clock.value < until) continue;
        sprite.visible = false;
        const pool = idle.get(sprite.material as THREE.SpriteNodeMaterial) ?? [];
        idle.set(sprite.material as THREE.SpriteNodeMaterial, pool);
        pool.push(sprite);
        busy.splice(i, 1);
      }
      for (const flash of flashes) {
        if (!flash.light.visible) continue;
        const t = (clock.value - flash.born) / 0.45;
        flash.light.intensity = flash.strength * Math.max(0, 1 - t) ** 2;
        if (t >= 1) flash.light.visible = false;
      }
      // Rain comes on and goes off over a couple of seconds.
      rainLevel.value += ((raining ? 1 : 0) - rainLevel.value) * Math.min(1, dt * 1.2);
      if (!raining && rainLevel.value < 0.01) rainLevel.value = 0;
      drops.visible = rainLevel.value > 0;
      const look = frame++ % 10 === 0;
      for (const effect of placed) {
        if (look) {
          const distance = effect.position.distanceTo(eye);
          effect.near = effect.map.shown && distance < (effect.near ? DISTANCE + SLACK : DISTANCE);
        }
        const wanted = effect.on && effect.near ? 1 : 0;
        // Out at once with a map that is hidden (a door was used, behind a fade); otherwise eased.
        effect.level = effect.map.shown ? effect.level + (wanted - effect.level) * Math.min(1, dt * 2.5) : 0;
        const showing = effect.level > 0.01;
        effect.group.visible = showing;
        for (const part of effect.parts) part.userData.level = effect.level;
        if (effect.light) {
          effect.light.visible = showing;
          const t = clock.value + effect.phase;
          const flicker = 0.82 + 0.1 * Math.sin(t * 11.3) + 0.08 * Math.sin(t * 23.7 + 1.3);
          effect.light.intensity = effect.strength * flicker * effect.level;
        }
      }
    },
  };
}
