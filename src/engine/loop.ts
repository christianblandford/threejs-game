import * as THREE from "three/webgpu";

/**
 * The frame loop: three's own animation loop and frame timer.
 *
 * `step` is called once a frame with the time to give the world (seconds,
 * never more than a tenth: a long stall is not a long leap) and how long
 * the frame took to arrive (ms). A measurement can hold the loop (`held`)
 * and draw frames by hand in its place.
 */
export interface Loop {
  /** While true, the loop does nothing: something else is drawing. */
  held: boolean;
  /** How long a frame takes to arrive, smoothed (ms). */
  readonly frameTime: number;
  /** Frames since the start. */
  readonly frames: number;
  start(step: (seconds: number, interval: number) => void): void;
}

export function createLoop(renderer: THREE.WebGPURenderer): Loop {
  let last = performance.now();
  let smoothed = 16;
  let frames = 0;
  // three's own frame timer: it leaves out the time a hidden page was not drawing.
  const timer = new THREE.Timer();
  timer.connect(document);
  const loop: Loop = {
    held: false,
    get frameTime() {
      return smoothed;
    },
    get frames() {
      return frames;
    },
    start(step) {
      renderer.setAnimationLoop(() => {
        if (loop.held) return;
        const now = performance.now();
        const interval = now - last;
        smoothed += (interval - smoothed) * 0.05;
        last = now;
        timer.update();
        step(Math.min(0.1, timer.getDelta()), interval);
        frames++;
      });
    },
  };
  return loop;
}
