import * as THREE from "three/webgpu";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { disposeObject } from "../engine/dispose";
import type { Physics } from "../engine/physics";

/**
 * The player: the sample fox, walked, swum and looked through.
 *
 * Movement is Rapier's own character controller pushing a capsule: it slides
 * along walls, climbs kerbs and steps by itself, sticks to the ground on the
 * way down a slope, and reports whether it is standing on anything. Gravity,
 * jumping and swimming are done here.
 *
 * Two views. In third person the camera follows from behind and above and
 * comes closer when something is between it and the fox; in first person it
 * sits at the fox's head. V switches, and so does the wheel: zooming in past
 * the closest distance goes into the fox's head, zooming out comes back.
 *
 * Swimming starts by itself where the water is deep enough to float in (the
 * map says where water is and how high) and ends when the feet find the
 * bottom. A jump from the water is how to get out onto a bank.
 */

export interface PlayerInput {
  /** Sideways and forward, each -1 to 1, relative to where the camera looks. */
  move: { x: number; y: number };
  run: boolean;
  jump: boolean;
}
export type ViewMode = "first" | "third";

export interface Player {
  /** Where the feet are. */
  readonly position: THREE.Vector3;
  readonly grounded: boolean;
  readonly swimming: boolean;
  /** The heading the camera looks along (0 looks down -z). */
  readonly yaw: number;
  /** True: stands still whatever is pressed (a conversation, a door). Looking around still works. */
  frozen: boolean;
  readonly viewMode: ViewMode;
  setViewMode(mode: ViewMode): void;
  /** Turns the camera: radians, from mouse movement. */
  look(yaw: number, pitch: number): void;
  /** Wheel steps: positive is out. */
  zoom(steps: number): void;
  /** Where the camera looks, up or down (radians). */
  readonly pitch: number;
  /** Sets where the camera looks outright (a replayed tick's input says where it was looking). */
  setLook(yaw: number, pitch: number): void;
  /** Puts the fox somewhere, facing `yaw`, and the camera with it. */
  teleport(feet: THREE.Vector3, yaw?: number): void;
  /** One tick of the fox itself: where it is, how fast it moves, whether it stands, swims or falls. Draws nothing. */
  simulate(dt: number, input: PlayerInput): void;
  /**
   * Puts the camera where this tick's view is, exactly: at its full distance
   * (or against whatever is in the way), with none of the easing that
   * drawing has. What a tick judges by the camera (what "use" would use) is
   * judged from here, so it depends on the state of play and on nothing
   * about how often the screen was drawn.
   */
  aim(): void;
  /**
   * Draws the fox and places the camera, `alpha` of the way from where it
   * was a tick ago to where it is now, with `seconds` passed for what only
   * moves on screen (the legs, the bob in water, the camera easing).
   */
  present(seconds: number, alpha: number): void;
  /** A tick and its drawing in one: for stepping the fox by hand. */
  update(dt: number, input: PlayerInput): void;
  /** Everything about the fox that the next tick depends on, as plain data; and putting it back. */
  snapshot(): PlayerSnapshot;
  restore(snapshot: PlayerSnapshot): void;
  /** Takes the fox out of the scene and the physics. */
  dispose(): void;
  onJump: (() => void) | null;
  /** With the speed (m/s) the ground was met at. */
  onLand: ((impactSpeed: number) => void) | null;
  /** On entering the water (with the speed it was hit at) and on leaving it. */
  onSwimChanged: ((change: { swimming: boolean; speed: number }) => void) | null;
}

export interface PlayerSnapshot {
  position: [number, number, number];
  velocity: [number, number, number];
  fall: number;
  grounded: boolean;
  facing: number;
  yaw: number;
  pitch: number;
  mode: ViewMode;
  distance: number;
  swimming: boolean;
  surface: number;
  wantJump: number;
  coyote: number;
  airTime: number;
  arriving: number;
}

export interface PlayerOptions {
  /** The height of the water's surface at a position, or null where there is none. Without it the fox never swims. */
  water?: (position: THREE.Vector3) => number | null;
}

const MODEL = {
  url: "/assets/models/Fox.glb",
  height: 0.62,
  idle: "Survey",
  walk: "Walk",
  run: "Run",
  /** Ground speed each clip was animated for, in the file's own units per second: what the legs' speed is matched to. */
  walkClipSpeed: 120,
  runClipSpeed: 220,
  /** The run cycle's speed as a paddle: treading water, and swimming flat out. */
  paddleRates: [0.5, 0.85] as const,
};
/** Limits on how slow or fast a clip is played when matching the legs to the ground speed. */
const MIN_PLAYBACK_RATE = 0.5;
const MAX_PLAYBACK_RATE = 2.5;
const RADIUS = 0.2;
/** The capsule's straight part, half of it. With the round ends it stands 0.66 m. */
const HALF = 0.13;
const WALK = 2.6;
const RUN = 6.2;
const JUMP = 4.6;
const GRAVITY = 14;
/** How quickly speed and facing catch up with what is asked (per second). */
const QUICKNESS = 10;

// --- Views ---
/** What the third-person camera looks at, and where the first-person one sits (m above the feet). */
const PIVOT_HEIGHT = 0.62;
const EYE_HEIGHT = 0.5;
/** In first person the eye is at the head, this far ahead of the body's middle. */
const HEAD_FORWARD = 0.2;
const MIN_DISTANCE = 1.5;
const MAX_DISTANCE = 10;
const ZOOM_STEP = 0.15;
/** Zooming in this far below the closest distance means "keep going": first person. */
const FIRST_PERSON_ZOOM = 0.9;
/** The fox is not drawn when the camera is nearer than this: nobody wants to see the inside of its head. */
const HIDE_WITHIN = 0.55;
const MAX_PITCH = 1.4;

// --- Swimming ---
/** How much of the fox is under water while it floats: metres from the feet up to the waterline. */
const FLOAT_DEPTH = 0.42;
/** Swimming speed as a fraction of the walking or running speed. */
const SWIM_SPEED = 0.55;
/** Swimming starts this much (m) deeper than it stops, so the water's edge does not flicker between the two. */
const SWIM_ENTER_MARGIN = 0.03;
const SWIM_EXIT_MARGIN = 0.06;
/** How fast buoyancy brings the fox to its floating height, per second, and the most it will rise or sink (m/s). */
const BUOYANCY_RESPONSE = 5;
const BUOYANCY_MAX_SPEED = 1.6;
/** How fast water slows the fox to its swimming pace, per second. */
const WATER_DRAG = 4;
/** Moving up faster than this (m/s), it is jumping out of the water, not in it. */
const SWIM_LEAVE_SPEED = 0.6;
/** How the body rides the water: nose up by this much (radians), bobbing by up to about this much (m). */
const SWIM_TILT = -0.14;
const SWIM_BOB = 0.035;

/** After being put somewhere, this long (s) passes before a landing or a splash is reported: nobody fell. */
const ARRIVAL_GRACE = 0.6;

const clamp = (value: number, min: number, max: number): number => Math.min(Math.max(value, min), max);
/** Fraction of a gap to close this frame so easing looks the same at any frame rate. */
const easing = (rate: number, dt: number): number => 1 - Math.exp(-rate * dt);

export async function createPlayer(scene: THREE.Scene, camera: THREE.PerspectiveCamera, physics: Physics, start: THREE.Vector3, startYaw = 0, options: PlayerOptions = {}): Promise<Player> {
  const { RAPIER, world } = physics;
  const gltf = await new GLTFLoader().loadAsync(MODEL.url);
  const model = gltf.scene;
  const bounds = new THREE.Box3().setFromObject(model);
  const scale = MODEL.height / (bounds.max.y - bounds.min.y);
  model.scale.setScalar(scale);
  const modelY = -bounds.min.y * scale;
  model.position.y = modelY;
  model.traverse((object) => {
    object.castShadow = true;
    object.receiveShadow = true;
    object.frustumCulled = false; // a skinned mesh's bounds are its rest pose
  });
  // The fox faces +z in its file; `root` is what is turned and moved.
  const root = new THREE.Group();
  root.name = "player";
  root.add(model);
  scene.add(root);

  const mixer = new THREE.AnimationMixer(model);
  const clip = (name: string): THREE.AnimationAction | null => {
    const found = THREE.AnimationClip.findByName(gltf.animations, name);
    if (!found) return null;
    const action = mixer.clipAction(found);
    action.play();
    action.setEffectiveWeight(0);
    return action;
  };
  const idle = clip(MODEL.idle);
  const walk = clip(MODEL.walk);
  const run = clip(MODEL.run);

  const centreHeight = HALF + RADIUS;
  const body = world.createRigidBody(RAPIER.RigidBodyDesc.kinematicPositionBased().setTranslation(start.x, start.y + centreHeight, start.z));
  const capsule = world.createCollider(RAPIER.ColliderDesc.capsule(HALF, RADIUS), body);
  const controller = world.createCharacterController(0.02);
  controller.setUp({ x: 0, y: 1, z: 0 });
  controller.enableAutostep(0.3, 0.12, false); // door steps, kerbs
  controller.enableSnapToGround(0.25);
  controller.setMaxSlopeClimbAngle(THREE.MathUtils.degToRad(50));
  controller.setMinSlopeSlideAngle(THREE.MathUtils.degToRad(58));

  const position = start.clone();
  /** Where the fox was a tick ago, and where it is drawn: between the two. */
  const before = start.clone();
  const drawn = start.clone();
  let facingBefore = startYaw;
  /** Ground speed, for the legs. */
  let going = 0;
  const velocity = new THREE.Vector3();
  let grounded = false;
  let fall = 0;
  let facing = startYaw;
  let yaw = startYaw;
  let pitch = -0.22;
  let mode: ViewMode = "third";
  /** How far back the third-person camera is asked to be, and how far back it is. */
  let distance = 3.4;
  let shown = distance;
  /** A press of jump is remembered this long, so one made just before landing still counts. */
  let wantJump = 0;
  /** And leaving the ground is forgiven this long, so a jump at the very edge still works. */
  let coyote = 0;
  let swimming = false;
  let surface = 0;
  /** 0 on land, 1 afloat: eases between, for the bob and the body's tilt. */
  let afloat = 0;
  let swimTime = 0;
  let airTime = 0;
  let arriving = 0;

  const wish = new THREE.Vector3();
  const target = new THREE.Vector3();
  const back = new THREE.Vector3();
  const eye = new THREE.Vector3();
  const ahead = new THREE.Vector3();

  function setViewMode(next: ViewMode): void {
    if (next === mode) return;
    mode = next;
    // Zooming into first person leaves the zoom just under the minimum; coming back starts from it.
    if (mode === "third") distance = Math.max(distance, MIN_DISTANCE);
    else pitch = clamp(pitch, -MAX_PITCH, MAX_PITCH);
  }

  function placeCamera(dt: number): void {
    // The pivot slides down to the eye as the camera closes in, so arriving in first person does not end with a jump.
    const closeness = 1 - clamp(shown / MIN_DISTANCE, 0, 1);
    target.copy(drawn).y += PIVOT_HEIGHT + (EYE_HEIGHT - PIVOT_HEIGHT) * closeness;
    // Behind and above, by yaw and pitch (pitch below zero looks down from above).
    back.set(Math.sin(yaw) * Math.cos(pitch), -Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch));
    const wanted = mode === "third" ? clamp(distance, MIN_DISTANCE, MAX_DISTANCE) : 0;
    // Everything eases (zooming, switching views, backing out again), except
    // getting in front of something in the way: never show the inside of a wall.
    shown += (wanted - shown) * easing(10, dt);
    if (Math.abs(wanted - shown) < 0.005) shown = wanted;
    if (shown > 0.01) {
      const clear = physics.raycast(target, back, shown + 0.3, capsule);
      if (clear !== null) shown = Math.min(shown, Math.max(0, clear - 0.3));
    }
    ahead.set(-Math.sin(yaw), 0, -Math.cos(yaw)).multiplyScalar(HEAD_FORWARD * closeness);
    eye.copy(target).addScaledVector(back, shown).add(ahead);
    camera.position.copy(eye);
    camera.lookAt(eye.sub(back));
    model.visible = shown > HIDE_WITHIN;
  }

  /** Set once the fox has been taken out of the world: its body is gone, and Rapier does not forgive being asked about it. */
  let disposed = false;
  function teleport(feet: THREE.Vector3, toYaw?: number): void {
    if (disposed) return; // asked for by something that outlived this game (a door still being gone through)
    position.copy(feet);
    body.setTranslation({ x: feet.x, y: feet.y + centreHeight, z: feet.z }, true);
    body.setNextKinematicTranslation({ x: feet.x, y: feet.y + centreHeight, z: feet.z });
    physics.changed();
    velocity.set(0, 0, 0);
    fall = 0;
    airTime = 0;
    arriving = ARRIVAL_GRACE;
    if (toYaw !== undefined) facing = yaw = toYaw;
    // Put, not moved: nothing to draw between.
    before.copy(position);
    drawn.copy(position);
    facingBefore = facing;
    root.position.copy(position);
    root.rotation.y = facing + Math.PI;
    shown = mode === "third" ? clamp(distance, MIN_DISTANCE, MAX_DISTANCE) : 0;
    placeCamera(1);
  }
  teleport(start, startYaw);
  arriving = ARRIVAL_GRACE;

  /** True when the water here is deep enough to float in. */
  function deepEnoughToSwim(): boolean {
    const level = options.water?.(position) ?? null;
    if (level === null) return false;
    surface = level;
    const depth = level - position.y;
    // Floating holds the feet `FLOAT_DEPTH` below the surface: swimming starts a little deeper than it stops.
    if (swimming) return depth > FLOAT_DEPTH - SWIM_EXIT_MARGIN;
    return depth > FLOAT_DEPTH + SWIM_ENTER_MARGIN && fall < SWIM_LEAVE_SPEED;
  }

  const api: Player = {
    position,
    frozen: false,
    onJump: null,
    onLand: null,
    onSwimChanged: null,
    get yaw() {
      return yaw;
    },
    get grounded() {
      return grounded;
    },
    get swimming() {
      return swimming;
    },
    get viewMode() {
      return mode;
    },
    setViewMode,
    look(byYaw, byPitch) {
      yaw -= byYaw;
      // Third person looks down from above more than up from below; first person looks anywhere.
      pitch = mode === "first" ? clamp(pitch - byPitch, -MAX_PITCH, MAX_PITCH) : clamp(pitch - byPitch, -1.25, 0.6);
    },
    zoom(steps) {
      if (mode === "first") {
        // Scrolling out of first person lands at the closest third-person distance.
        if (steps > 0) {
          distance = MIN_DISTANCE;
          setViewMode("third");
        }
        return;
      }
      // The zoom may dip a little below the minimum (the camera stays at the minimum); far enough means first person.
      distance = Math.min(distance * Math.pow(1 + ZOOM_STEP, steps), MAX_DISTANCE);
      if (distance < MIN_DISTANCE * FIRST_PERSON_ZOOM) setViewMode("first");
    },
    get pitch() {
      return pitch;
    },
    setLook(toYaw, toPitch) {
      yaw = toYaw;
      pitch = toPitch;
    },
    teleport,
    update(dt, input) {
      api.simulate(dt, input);
      api.present(dt, 1);
    },
    snapshot: () => ({
      position: position.toArray(),
      velocity: velocity.toArray(),
      fall,
      grounded,
      facing,
      yaw,
      pitch,
      mode,
      distance,
      swimming,
      surface,
      wantJump,
      coyote,
      airTime,
      arriving,
    }),
    restore(saved) {
      position.fromArray(saved.position);
      velocity.fromArray(saved.velocity);
      ({ fall, grounded, facing, yaw, pitch, mode, distance, swimming, surface, wantJump, coyote, airTime, arriving } = saved);
      body.setTranslation({ x: position.x, y: position.y + centreHeight, z: position.z }, true);
      body.setNextKinematicTranslation({ x: position.x, y: position.y + centreHeight, z: position.z });
      physics.changed();
      if (swimming) controller.disableSnapToGround();
      else controller.enableSnapToGround(0.25);
      before.copy(position);
      drawn.copy(position);
      facingBefore = facing;
      shown = mode === "third" ? clamp(distance, MIN_DISTANCE, MAX_DISTANCE) : 0;
      api.present(0, 1);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      mixer.stopAllAction();
      world.removeCharacterController(controller);
      world.removeRigidBody(body);
      physics.changed();
      disposeObject(root);
    },
    aim() {
      const reach = mode === "third" ? clamp(distance, MIN_DISTANCE, MAX_DISTANCE) : 0;
      const closeness = 1 - clamp(reach / MIN_DISTANCE, 0, 1);
      target.copy(position).y += PIVOT_HEIGHT + (EYE_HEIGHT - PIVOT_HEIGHT) * closeness;
      back.set(Math.sin(yaw) * Math.cos(pitch), -Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch));
      const clear = reach > 0.01 ? physics.raycast(target, back, reach + 0.3, capsule) : null;
      ahead.set(-Math.sin(yaw), 0, -Math.cos(yaw)).multiplyScalar(HEAD_FORWARD * closeness);
      eye.copy(target).addScaledVector(back, clear === null ? reach : Math.min(reach, Math.max(0, clear - 0.3))).add(ahead);
      camera.position.copy(eye);
      camera.lookAt(eye.sub(back));
    },
    simulate(dt, input) {
      before.copy(position);
      facingBefore = facing;
      arriving = Math.max(0, arriving - dt);

      // In the water, or out of it?
      const wasSwimming = swimming;
      const inWater = deepEnoughToSwim();
      wantJump = input.jump && !api.frozen ? 0.14 : Math.max(0, wantJump - dt);
      if (inWater && !(swimming && wantJump > 0)) swimming = true;
      else if (swimming) {
        swimming = false;
        // A jump from the water is how to get out onto a bank or a ledge.
        if (inWater && wantJump > 0) {
          fall = JUMP;
          wantJump = 0;
          api.onJump?.();
        }
      }
      if (swimming !== wasSwimming) {
        if (swimming) controller.disableSnapToGround();
        else controller.enableSnapToGround(0.25);
        if (arriving === 0) api.onSwimChanged?.({ swimming, speed: swimming ? Math.abs(fall) : 0 });
      }

      // Where the player wants to go, relative to the camera's heading.
      const forward = api.frozen ? 0 : input.move.y;
      const side = api.frozen ? 0 : input.move.x;
      wish.set(-Math.sin(yaw) * forward + Math.cos(yaw) * side, 0, -Math.cos(yaw) * forward - Math.sin(yaw) * side);
      const asked = Math.min(1, wish.length());
      if (asked > 0) wish.normalize();
      const speed = (input.run ? RUN : WALK) * asked * (swimming ? SWIM_SPEED : 1);

      if (swimming) {
        // Water drags the fox toward its swimming pace, and buoyancy toward
        // its floating height. Easing the vertical speed, not setting it,
        // lets a dive carry under for a moment before bobbing up.
        const drag = easing(WATER_DRAG, dt);
        velocity.x += (wish.x * speed - velocity.x) * drag;
        velocity.z += (wish.z * speed - velocity.z) * drag;
        const rise = clamp((surface - FLOAT_DEPTH - position.y) * BUOYANCY_RESPONSE, -BUOYANCY_MAX_SPEED, BUOYANCY_MAX_SPEED);
        fall += (rise - fall) * easing(BUOYANCY_RESPONSE * 1.5, dt);
        coyote = 0;
      } else {
        const blend = Math.min(1, dt * QUICKNESS * (grounded ? 1 : 0.25)); // little steering in the air
        velocity.x += (wish.x * speed - velocity.x) * blend;
        velocity.z += (wish.z * speed - velocity.z) * blend;
        coyote = grounded ? 0.1 : Math.max(0, coyote - dt);
        if (wantJump > 0 && coyote > 0) {
          fall = JUMP;
          wantJump = coyote = 0;
          api.onJump?.();
        } else if (grounded && fall <= 0) {
          fall = -1; // pressed gently onto the ground, so the controller keeps reporting it
        } else {
          fall -= GRAVITY * dt;
        }
      }

      const falling = fall;
      controller.computeColliderMovement(capsule, { x: velocity.x * dt, y: fall * dt, z: velocity.z * dt });
      const moved = controller.computedMovement();
      const wasGrounded = grounded;
      grounded = controller.computedGrounded() && !swimming;
      if (fall > 0 && moved.y < fall * dt * 0.5) fall = 0; // hit a ceiling
      const now = body.translation();
      body.setNextKinematicTranslation({ x: now.x + moved.x, y: now.y + moved.y, z: now.z + moved.z });
      position.set(now.x + moved.x, now.y + moved.y - centreHeight, now.z + moved.z);

      // Back on the ground after time in the air: a landing, as hard as the fall was fast.
      if (grounded && !wasGrounded && airTime > 0.15 && arriving === 0) api.onLand?.(Math.max(0, -falling));
      airTime = grounded || swimming ? 0 : airTime + dt;

      // In first person the body looks where the camera looks; otherwise it faces the way it is going, turning the short way round.
      going = Math.hypot(velocity.x, velocity.z);
      if (mode === "first") facing = yaw;
      else if (going > 0.3) {
        const heading = Math.atan2(-velocity.x, -velocity.z);
        const turn = heading - facing;
        facing += Math.atan2(Math.sin(turn), Math.cos(turn)) * Math.min(1, dt * QUICKNESS);
      }
    },
    present(dt, alpha) {
      // Between where it was a tick ago and where it is, turning the short way round.
      drawn.lerpVectors(before, position, alpha);
      const turned = facing - facingBefore;
      root.position.copy(drawn);
      root.rotation.y = facingBefore + Math.atan2(Math.sin(turned), Math.cos(turned)) * alpha + Math.PI;

      // Afloat, the body rides the water: a slow bob, a slight roll, the nose a little up.
      afloat += ((swimming ? 1 : 0) - afloat) * easing(6, dt);
      if (afloat < 1e-3) afloat = 0;
      swimTime += dt;
      model.position.y = modelY + (Math.sin(swimTime * 3.7) * 0.7 + Math.sin(swimTime * 2.3 + 1.3) * 0.3) * SWIM_BOB * afloat;
      model.rotation.z = Math.sin(swimTime * 1.9) * 0.06 * afloat;
      model.rotation.x = SWIM_TILT * afloat;

      // Idle, walk and run all play; speed sets how much of each shows, and
      // how fast the legs go. The fox has no swimming clip: its run, slowed
      // down, makes a passable paddle, slowest when treading water.
      // Each clip was animated for one ground speed (in the model's own
      // units, so scaled with it): played faster or slower in proportion, the
      // feet keep up with the ground and do not slide. Idle gives way to walk
      // over the range where the walk cannot be played slowly enough.
      const walkClipSpeed = MODEL.walkClipSpeed * scale;
      const runClipSpeed = MODEL.runClipSpeed * scale;
      const stride = clamp(going / (walkClipSpeed * MIN_PLAYBACK_RATE), 0, 1);
      const sprint = clamp((going - WALK) / (RUN - WALK), 0, 1);
      const stroke = clamp(going / (RUN * SWIM_SPEED), 0, 1);
      const dry = 1 - afloat;
      const rate = (speedRatio: number): number => clamp(speedRatio, MIN_PLAYBACK_RATE, MAX_PLAYBACK_RATE);
      idle?.setEffectiveWeight((1 - stride) * dry);
      walk?.setEffectiveWeight(stride * (1 - sprint) * dry);
      run?.setEffectiveWeight(stride * sprint * dry + afloat);
      walk?.setEffectiveTimeScale(rate(going / walkClipSpeed));
      const paddle = MODEL.paddleRates[0] + (MODEL.paddleRates[1] - MODEL.paddleRates[0]) * stroke;
      run?.setEffectiveTimeScale(rate(going / runClipSpeed) * dry + paddle * afloat);
      mixer.update(dt);

      placeCamera(dt);
    },
  };
  return api;
}
