import * as THREE from "three/webgpu";
import type { Input } from "../engine/input";

/**
 * Flying the camera freely instead of walking about as the fox: F switches,
 * and the measuring hooks fly. `?fly` starts that way.
 */
export interface FlyCamera {
  active: boolean;
  /** Switches between flying and walking. Taking off starts from where the camera is, looking the way it looks. */
  toggle(): void;
  /** Puts the camera somewhere, looking a given way, and leaves it flying. */
  place(position: THREE.Vector3, yaw: number, pitch: number): void;
  /** A frame of flying: turned by the mouse or stick, moved by the movement keys. */
  update(seconds: number, turned: { yaw: number; pitch: number }, input: Input): void;
}

export function createFlyCamera(camera: THREE.PerspectiveCamera, active: boolean): FlyCamera {
  let yaw = 0;
  let pitch = 0;
  const fly: FlyCamera = {
    active,
    toggle() {
      fly.active = !fly.active;
      if (!fly.active) return;
      const facing = camera.getWorldDirection(new THREE.Vector3());
      yaw = Math.atan2(-facing.x, -facing.z);
      pitch = Math.asin(THREE.MathUtils.clamp(facing.y, -1, 1));
    },
    place(position, toYaw, toPitch) {
      fly.active = true;
      camera.position.copy(position);
      yaw = toYaw;
      pitch = toPitch;
      camera.rotation.set(pitch, yaw, 0, "YXZ");
    },
    update(seconds, turned, input) {
      yaw += turned.yaw;
      pitch = Math.max(-1.4, Math.min(1.4, pitch + turned.pitch));
      camera.rotation.set(pitch, yaw, 0, "YXZ");
      const speed = (input.isDown("sprint") ? 14 : 4) * seconds;
      const forward = input.move.y;
      const side = input.move.x;
      camera.position.x += (-Math.sin(yaw) * forward + Math.cos(yaw) * side) * speed;
      camera.position.z += (-Math.cos(yaw) * forward - Math.sin(yaw) * side) * speed;
    },
  };
  return fly;
}
