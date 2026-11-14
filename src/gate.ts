import * as THREE from "three/webgpu";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { Hud } from "./hud";
import type { Interact } from "./interact";
import type { LoadedMap } from "./map";
import type { Physics } from "./physics";
import { markerPosition, markerYaw } from "./spaces";
import type { WorldState } from "./worldState";

/**
 * The town's portcullis. Blender marks where it goes and how big it is (the
 * Empty with `kind = "portcullis"`) and where its lever is (`interact =
 * "gate_lever"`); the moving part is built here. It is a kinematic body: the
 * game moves it and nothing gets through while it is down. Whether it is
 * down is kept in the saved world (`gate.lowered` under the level's name),
 * where the steward's story reads it.
 */

/** How fast the gate moves (m/s). */
const SPEED = 1.8;

export interface Gate {
  update(dt: number): void;
}

export function createGate(systems: {
  map: LoadedMap;
  physics: Physics;
  interact: Interact;
  hud: Hud;
  state: WorldState;
  feet: THREE.Vector3;
  /** Told when the gateway closes to those who walk by themselves (the gate is down), and when it opens again. */
  block?(box: { centre: THREE.Vector3; half: THREE.Vector3; rotation: THREE.Quaternion } | null): void;
  /** Told when the lever is pulled and when the gate comes to rest: for a sound. */
  moved?(what: "lever" | "arrived"): void;
}): Gate | null {
  const { map, physics, interact, hud, state, feet } = systems;
  const marker = map.manifest.markers.find((found) => found.props.kind === "portcullis");
  if (!marker) return null;
  const [width, height] = marker.size ?? [6, 4.4, 0.3];

  const metal = new THREE.MeshStandardNodeMaterial({ color: 0x1b1b1e, metalness: 0.6, roughness: 0.5 });
  const parts: THREE.BufferGeometry[] = [];
  for (let x = -width / 2 + 0.25; x < width / 2; x += 0.5) parts.push(new THREE.BoxGeometry(0.12, height, 0.12).translate(x, 0, 0));
  for (let y = -height / 2 + 0.3; y < height / 2; y += 0.8) parts.push(new THREE.BoxGeometry(width, 0.1, 0.2).translate(0, y, 0));
  const gate = new THREE.Mesh(mergeGeometries(parts), metal);
  gate.name = "portcullis";
  gate.castShadow = gate.receiveShadow = true;
  gate.rotation.y = markerYaw(marker);
  map.root.add(gate);

  // The marker sits on the ground in the middle of the opening.
  const ground = markerPosition(marker);
  const closedY = ground.y + height / 2;
  const lift = height - 0.5; // clear of a walker's head, hidden in the stonework above
  let raised = state.get("gate.lowered", false) ? 0 : lift;
  let target = raised;
  gate.position.set(ground.x, closedY + raised, ground.z);

  const { RAPIER, world } = physics;
  const turn = new THREE.Quaternion().setFromEuler(gate.rotation);
  const body = world.createRigidBody(RAPIER.RigidBodyDesc.kinematicPositionBased().setTranslation(gate.position.x, gate.position.y, gate.position.z).setRotation(turn));
  world.createCollider(RAPIER.ColliderDesc.cuboid(width / 2, height / 2, 0.15), body);
  // Where the gate stands when it is down: what navigation is given to walk around.
  const closed = { centre: new THREE.Vector3(ground.x, closedY, ground.z), half: new THREE.Vector3(width / 2, height / 2, 0.15), rotation: turn };
  if (target === 0) systems.block?.(closed);

  // A lever to look at, where the thing to use is.
  const handles: THREE.Object3D[] = [];
  const levers = interact.all("interact", "gate_lever");
  for (const lever of levers) {
    lever.prompt = target === 0 ? "Raise the gate" : "Lower the gate";
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 1), metal);
    post.position.copy(lever.position).y -= 0.5;
    post.castShadow = true;
    const handle = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.55, 0.07).translate(0, 0.275, 0), metal);
    handle.position.y = 0.45;
    handle.rotation.x = target === 0 ? 0.6 : -0.6;
    post.add(handle);
    map.root.add(post);
    handles.push(handle);
  }

  interact.on("interact", (used) => {
    if (used.name !== "gate_lever") return;
    const lowering = target !== 0;
    // Not onto anyone's head.
    const inTheWay = Math.abs(feet.x - ground.x) < width / 2 + 0.3 && Math.abs(feet.z - ground.z) < 1 && feet.y < ground.y + height;
    if (lowering && inTheWay) {
      hud.toast("Something is in the gateway.");
      return;
    }
    target = lowering ? 0 : lift;
    state.set("gate.lowered", lowering);
    if (!lowering) systems.block?.(null); // passable again as soon as it starts to rise
    systems.moved?.("lever");
    for (const lever of levers) lever.prompt = lowering ? "Raise the gate" : "Lower the gate";
  });

  return {
    update(dt) {
      for (const handle of handles) {
        const wanted = target === 0 ? 0.6 : -0.6;
        handle.rotation.x += Math.sign(wanted - handle.rotation.x) * Math.min(Math.abs(wanted - handle.rotation.x), dt * 4);
      }
      if (raised === target) return;
      const step = SPEED * dt;
      raised = raised < target ? Math.min(target, raised + step) : Math.max(target, raised - step);
      gate.position.y = closedY + raised;
      body.setNextKinematicTranslation({ x: gate.position.x, y: gate.position.y, z: gate.position.z });
      if (raised !== target) return;
      hud.toast(target === 0 ? "The gate is down." : "The gate is up.");
      systems.moved?.("arrived");
      if (target === 0) systems.block?.(closed);
    },
  };
}
