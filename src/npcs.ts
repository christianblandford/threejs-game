import * as THREE from "three/webgpu";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type { NavAgent, Navigation } from "./navigation";
import type { Physics } from "./physics";

/**
 * A few foxes that wander about and now and then trot over to the player: a
 * small example of putting characters on the navmesh (navigation.ts). Ported
 * from the Babylon project's src/game/npcs.ts.
 *
 * Each one is a nav agent, which decides where the feet go, plus a model that
 * follows it. Everything advances by the time handed to `update`.
 */

export interface NpcOptions {
  /** What they are added to: the map they live in, so that they are hidden with it. */
  parent: THREE.Object3D;
  navigation: Navigation;
  physics: Physics;
  /** They start in a small ring around this point. */
  around: THREE.Vector3;
  count?: number;
  /** Someone to visit from time to time: anything with a position, usually the player. */
  player?: { readonly position: THREE.Vector3 };
}

export interface Npcs {
  readonly agents: NavAgent[];
  update(dt: number): void;
}

/** Everything specific to the model: the same fox as the player, a little smaller. */
const MODEL = {
  url: "/assets/models/Fox.glb",
  /** Metres from paws to ear tips. */
  height: 0.55,
  clips: { idle: "Survey", walk: "Walk", run: "Run" },
  /** Ground speed each clip was animated for, in the file's own units per second. */
  walkClipSpeed: 120,
  runClipSpeed: 220,
};

/** Metres per second. */
const WANDER_SPEED = 0.9;
const TROT_SPEED = 2.4;
/** How far from where it stands a fox picks its next spot to wander to. */
const WANDER_RADIUS = 12;
/** Each time a fox picks something to do, this is the chance it goes to see the player, if within range (m). */
const VISIT_CHANCE = 0.3;
const VISIT_RANGE = 40;
/** A visiting fox stops this far (m) from the player. */
const VISIT_DISTANCE = 1.5;
/** The room (m) the foxes leave around the player when passing. */
const PLAYER_RADIUS = 0.3;
/** Seconds between decisions: at least this, plus up to as much again. */
const DECISION_TIME = 4;
const TURN_RESPONSE = 8;
const BLEND_RESPONSE = 10;
const MIN_PLAYBACK_RATE = 0.5;
const MAX_PLAYBACK_RATE = 2.5;
/** How far above and below the navmesh (m) to look for the real ground. */
const GROUND_PROBE = 0.4;

interface Clip {
  action: THREE.AnimationAction | null;
  weight: number;
}
interface Npc {
  agent: NavAgent;
  /** At the feet; `rotation.y` is the way it faces. The model hangs underneath. */
  root: THREE.Group;
  mixer: THREE.AnimationMixer;
  scale: number;
  idle: Clip;
  walk: Clip;
  run: Clip;
  /** Seconds until it next decides what to do. */
  wait: number;
  visiting: boolean;
}

const clamp = (value: number, min: number, max: number): number => Math.min(Math.max(value, min), max);
/** Fraction of a gap to close this frame so easing looks the same at any frame rate. */
const easing = (rate: number, dt: number): number => 1 - Math.exp(-rate * dt);
const wrapAngle = (radians: number): number => Math.atan2(Math.sin(radians), Math.cos(radians));

/**
 * Spawns the foxes. They need no looking after: each stands still until there
 * is navmesh under it, and carries on by itself when the navmesh is rebuilt.
 */
export async function createNpcs(options: NpcOptions): Promise<Npcs> {
  const { parent, navigation, physics, around, count = 3, player } = options;
  const loader = new GLTFLoader();

  const npcs = await Promise.all(
    Array.from({ length: count }, async (_, i): Promise<Npc> => {
      const gltf = await loader.loadAsync(MODEL.url);
      const model = gltf.scene;
      // Scale by the model's measured height and stand its lowest point on the ground.
      const bounds = new THREE.Box3().setFromObject(model);
      const scale = MODEL.height / (bounds.max.y - bounds.min.y);
      model.scale.setScalar(scale);
      model.position.y = -bounds.min.y * scale;
      model.traverse((object) => {
        object.castShadow = true;
        object.receiveShadow = true;
        object.frustumCulled = false; // a skinned mesh's bounds are its rest pose
      });
      const root = new THREE.Group();
      root.name = `npc-${i}`;
      root.add(model);
      parent.add(root);

      // All three clips play all the time; only their weights change.
      const mixer = new THREE.AnimationMixer(model);
      const clip = (name: string, weight: number): Clip => {
        const found = THREE.AnimationClip.findByName(gltf.animations, name);
        const action = found ? mixer.clipAction(found) : null;
        action?.play();
        action?.setEffectiveWeight(weight);
        return { action, weight };
      };

      const angle = (i / count) * Math.PI * 2;
      const start = around.clone().add(new THREE.Vector3(Math.cos(angle) * 2.5, 0, Math.sin(angle) * 2.5));
      const agent = navigation.addAgent(start, { speed: WANDER_SPEED });
      root.position.copy(agent.position);
      root.rotation.y = angle;
      return {
        agent,
        root,
        mixer,
        scale,
        idle: clip(MODEL.clips.idle, 1),
        walk: clip(MODEL.clips.walk, 0),
        run: clip(MODEL.clips.run, 0),
        wait: 1 + Math.random() * DECISION_TIME, // not all at once
        visiting: false,
      };
    }),
  );

  // The player is not moved by the crowd, but the foxes should still walk
  // around them: an agent that is simply put where the player is, every
  // frame, is something for the others to avoid.
  const playerMarker = player ? navigation.addAgent(player.position, { radius: PLAYER_RADIUS }) : null;

  /** Picks something to do every few seconds: wander somewhere nearby, or go and see the player. */
  function think(npc: Npc, dt: number): void {
    const { agent } = npc;
    if (npc.visiting && player && agent.position.distanceTo(player.position) < VISIT_DISTANCE) {
      agent.stop();
      npc.visiting = false;
    }
    npc.wait -= dt;
    if (npc.wait > 0) return;
    const visit = !!player && Math.random() < VISIT_CHANCE && agent.position.distanceTo(player.position) < VISIT_RANGE;
    const target = visit ? player.position : navigation.randomPointAround(agent.position, WANDER_RADIUS);
    agent.speed = visit ? TROT_SPEED : WANDER_SPEED;
    // No target, or `goTo` false, means there is no navmesh there (or not yet): try again shortly.
    const going = target !== null && agent.goTo(target);
    npc.visiting = visit && going;
    npc.wait = going ? DECISION_TIME * (1 + Math.random()) : 1;
  }

  // The navmesh floats a few centimetres above the surface it was made from,
  // more on slopes. The agent says where to stand; a short ray down through
  // that spot finds the ground itself.
  const probe = new THREE.Vector3();
  const down = new THREE.Vector3(0, -1, 0);
  /** Puts the model where its agent is, feet on the ground, turning to face the way it is going. */
  function place(npc: Npc, speed: number, dt: number): void {
    const { agent, root } = npc;
    root.position.copy(agent.position);
    probe.copy(agent.position).y += GROUND_PROBE;
    const hit = physics.raycast(probe, down, GROUND_PROBE * 2);
    if (hit !== null) root.position.y = probe.y - hit;
    if (speed > 0.1) {
      const heading = Math.atan2(agent.velocity.x, agent.velocity.z); // the fox faces +z in its file
      root.rotation.y += wrapAngle(heading - root.rotation.y) * easing(TURN_RESPONSE, dt);
    }
  }

  /** Cross-fades idle, walk and run by actual speed, and matches leg speed to ground speed. */
  function animate(npc: Npc, speed: number, dt: number): void {
    const walkClipSpeed = MODEL.walkClipSpeed * npc.scale;
    const runClipSpeed = MODEL.runClipSpeed * npc.scale;
    const moving = clamp(speed / (walkClipSpeed * MIN_PLAYBACK_RATE), 0, 1);
    const running = clamp((speed - WANDER_SPEED) / (TROT_SPEED - WANDER_SPEED), 0, 1);
    const blend = easing(BLEND_RESPONSE, dt);
    const set = (clip: Clip, target: number, playbackRate: number): void => {
      clip.weight += (target - clip.weight) * blend;
      clip.action?.setEffectiveWeight(clip.weight);
      clip.action?.setEffectiveTimeScale(clamp(playbackRate, MIN_PLAYBACK_RATE, MAX_PLAYBACK_RATE));
    };
    set(npc.idle, 1 - moving, 1);
    set(npc.walk, moving * (1 - running), speed / walkClipSpeed);
    set(npc.run, moving * running, speed / runClipSpeed);
    npc.mixer.update(dt);
  }

  return {
    agents: npcs.map((npc) => npc.agent),
    /** Call after `navigation.update`, so that the agents have already moved this frame. */
    update(dt) {
      if (dt <= 0) return;
      if (player) playerMarker?.teleport(player.position);
      for (const npc of npcs) {
        const speed = Math.hypot(npc.agent.velocity.x, npc.agent.velocity.z);
        think(npc, dt);
        place(npc, speed, dt);
        animate(npc, speed, dt);
      }
    },
  };
}
