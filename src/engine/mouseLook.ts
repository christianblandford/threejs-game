import * as THREE from "three/webgpu";
import { PointerLockControls } from "three/addons/controls/PointerLockControls.js";
import { getSettings } from "./settings";

/**
 * Turning the view with the mouse, through three's own pointer-lock control.
 *
 * The control is given a stand-in to turn, not the camera: the player's
 * camera is placed by the player, which wants to know how far the mouse
 * turned this frame. So the stand-in is read and set straight again every
 * frame (`turned`).
 */
export interface MouseLook {
  /** Is the mouse captured? */
  readonly locked: boolean;
  /** Multiplier on how far a movement of the mouse turns the view. */
  sensitivity: number;
  /** How far the mouse has turned the view since last asked: radians left, and up. */
  turned(): { yaw: number; pitch: number };
  /** Asks the browser for the mouse. (Refused where the page is embedded in another: the game then plays without mouse look.) */
  capture(): void;
  release(): void;
  /** Called when the mouse is let go, by Escape or by `release`. The browser keeps that Escape to itself. */
  onReleased: (() => void) | null;
}

export function createMouseLook(canvas: HTMLCanvasElement): MouseLook {
  const rig = new THREE.PerspectiveCamera();
  const controls = new PointerLockControls(rig, canvas);
  controls.pointerSpeed = getSettings().lookSensitivity;
  const turn = new THREE.Euler(0, 0, 0, "YXZ");
  const look: MouseLook = {
    get locked() {
      return controls.isLocked;
    },
    get sensitivity() {
      return controls.pointerSpeed;
    },
    set sensitivity(value: number) {
      controls.pointerSpeed = value;
    },
    turned() {
      turn.setFromQuaternion(rig.quaternion, "YXZ");
      rig.quaternion.identity();
      return { yaw: turn.y, pitch: turn.x * (getSettings().invertY ? -1 : 1) };
    },
    capture: () => void Promise.resolve(canvas.requestPointerLock()).catch(() => undefined),
    release() {
      if (controls.isLocked) controls.unlock();
    },
    onReleased: null,
  };
  controls.addEventListener("unlock", () => look.onReleased?.());
  return look;
}
