import type * as THREE from "three/webgpu";
import { activeQuality, params } from "./params";
import { createResolutionGovernor, type ResolutionGovernor } from "./resolution";
import { getSettings, QUALITY } from "./settings";

/**
 * How many pixels the canvas is drawn with.
 *
 * Normally it follows the window: the quality setting's device pixels per
 * CSS pixel (`?ratio=` overrides it), times whatever the resolution
 * governor has decided the machine can hold (resolution.ts; `?autoscale=0`
 * holds it off). A test can pin it to an exact size instead, after which
 * neither the window nor the governor moves it.
 */
export interface Viewport {
  readonly governor: ResolutionGovernor;
  /** True once a test has fixed the size. */
  readonly pinned: boolean;
  /** Sizes the canvas to the window again. */
  fit(): void;
  /** Sets the canvas to an exact number of pixels, as the measurements do, and keeps it there. */
  pin(width: number, height: number): void;
  /** Lets the canvas follow the window again. */
  release(): void;
  /** Once a frame, with how long the last one took to arrive (ms): lets the governor act. */
  sample(interval: number): void;
  /** May the governor act? From the setting and the address. */
  refresh(): void;
}

export function createViewport(renderer: THREE.WebGPURenderer, canvas: HTMLCanvasElement, camera: THREE.PerspectiveCamera, onReshaped: () => void): Viewport {
  const governor = createResolutionGovernor();
  let pinned = false;

  function setPixels(width: number, height: number): void {
    renderer.setPixelRatio(1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    onReshaped();
  }
  function fit(): void {
    // A Retina screen has four times the pixels of an ordinary one and the eye
    // cannot count them: the picture is built from several frames and sharpened.
    const full = Math.min(window.devicePixelRatio, Number(params.get("ratio") ?? QUALITY[activeQuality()].pixelRatio));
    // Never under 0.7 of a CSS pixel each way: below that the picture is plainly soft.
    const ratio = Math.max(full * governor.scale, Math.min(full, 0.7));
    setPixels(Math.round(canvas.clientWidth * ratio), Math.round(canvas.clientHeight * ratio));
  }
  const refresh = (): void => {
    governor.enabled = params.get("autoscale") !== "0" && getSettings().autoResolution;
  };
  refresh();
  fit();
  window.addEventListener("resize", () => !pinned && fit());

  return {
    governor,
    get pinned() {
      return pinned;
    },
    fit,
    pin(width, height) {
      pinned = true;
      setPixels(width, height);
    },
    release() {
      pinned = false;
      fit();
    },
    sample(interval) {
      if (!pinned && governor.sample(interval)) fit();
    },
    refresh,
  };
}
