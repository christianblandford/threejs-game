import * as THREE from "three/webgpu";
import type { Engine } from "../engine/engine";
import type { FlyCamera } from "./flyCamera";
import type { Hud } from "./hud";
import type { Menu } from "./menu";
import type { Player } from "./player";

/**
 * The page's own events, tied to the game (returns what unties them): the mouse is captured while
 * playing and let go behind a menu, the wheel zooms, and a few keys do
 * things for whoever is developing (where the Babylon project has a debug
 * panel): R the weather, B an explosion a few steps ahead, F flying, N the
 * walkable area.
 */
export function addControls(systems: { engine: Engine; menu: Menu; player: Player; hud: Hud; fly: FlyCamera; onZoom(steps: number): void; onLeftPlay(): void }): () => void {
  // Every listener is added with this signal, and taken away again by aborting it.
  const listening = new AbortController();
  const { signal } = listening;
  const { engine, menu, player, fly } = systems;
  const { canvas, mouse, input, effects, audio, camera, navigation, scene } = engine;

  canvas.addEventListener("click", () => menu.state === "playing" && mouse.capture(), { signal });
  // Escape lets the mouse go, and the browser keeps that key press to itself: losing the mouse is the pause.
  mouse.onReleased = () => menu.pause();
  menu.onStateChanged = (now) => {
    if (now === "playing") mouse.capture();
    else mouse.release();
    if (now !== "playing") systems.onLeftPlay();
    input.releaseAll();
  };
  // The wheel is input like any other: it is handed to the next tick, not acted on here.
  canvas.addEventListener("wheel", (event) => menu.state === "playing" && systems.onZoom(Math.sign(event.deltaY)), { passive: true, signal });

  let navShown = false;
  window.addEventListener("keydown", (event) => {
    if (event.repeat) return;
    const playing = menu.state === "playing";
    if (event.code === "KeyN") navigation.showDebug((navShown = !navShown) ? scene : null);
    else if (event.code === "KeyF") fly.toggle();
    else if (event.code === "KeyR" && playing) effects.raining = !effects.raining;
    else if (event.code === "KeyB" && playing) {
      const ahead = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize().multiplyScalar(6).add(player.position);
      effects.burst("explosion", ahead);
      audio.play("land", { volume: 1, playbackRate: 0.35 });
    }
  }, { signal });

  return () => {
    listening.abort();
    mouse.onReleased = null;
    menu.onStateChanged = null;
    if (navShown) navigation.showDebug(null);
  };
}
