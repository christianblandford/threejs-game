import * as THREE from "three/webgpu";
import type { Engine } from "./engine";
import { activeQuality, params } from "./params";
import { perf } from "./perf";
import { drawByHand, gpuDone } from "./renderer";

/**
 * The measuring hooks behind `window.bench`: what a frame costs, what one
 * setting costs against another, how much the picture flickers, and where
 * the worst frames of a walk go. The README's "What was measured" and
 * "Measured again" are made with these.
 *
 * `hooks` is what a measurement needs from the game: to have the camera to
 * itself (`takeCamera`), and to move the world on as a frame would
 * (`advance`: streaming, particles, navigation, characters).
 */
export interface BenchHooks {
  takeCamera(): void;
  advance(seconds: number, eye: THREE.Vector3): void;
}

export function createBench(engine: Engine, hooks: BenchHooks) {
  const { renderer, canvas, camera, loop } = engine;
  const median = (values: number[]): number => +[...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]!.toFixed(1);

  /**
   * What a frame costs when frames are drawn one after another without
   * waiting: 20 frames are handed to the GPU and timed to the end, then 60,
   * and the difference is divided by the 40 between. `measure` below waits
   * for the GPU after every frame, and in some browsers that wait is itself
   * 50 ms or more; here it is paid once per run and cancels out.
   * `cpu` is the script's share of a frame.
   */
  async function pace(): Promise<{ frame: number; cpu: number }> {
    loop.held = true;
    const burst = async (count: number): Promise<[number, number]> => {
      const start = performance.now();
      for (let i = 0; i < count; i++) {
        drawByHand(renderer, engine.draw);
      }
      const handed = performance.now() - start;
      await gpuDone(renderer);
      return [performance.now() - start, handed / count];
    };
    await burst(10);
    const [short] = await burst(20);
    const [long, cpu] = await burst(60);
    loop.held = false;
    return { frame: +((long - short) / 40).toFixed(1), cpu: +cpu.toFixed(1) };
  }

  /**
   * Sets one of the `?name=value` switches as if it were in the address and
   * builds the picture again, so two settings can be timed back to back in
   * one page. (Two loads of the page differ by several milliseconds on
   * their own.) `null` takes the switch away. Only for what `compose` reads.
   */
  function tune(name: string, value: string | null): void {
    if (value === null) params.delete(name);
    else params.set(name, value);
    engine.picture.compose(activeQuality());
  }

  /**
   * Frame time with the GPU's share included, the way the Babylon project
   * measures it: draw a frame, wait for the GPU, time the whole.
   */
  async function measure(count = 120): Promise<{ cpu: number; total: number; draws: number; triangles: number }> {
    loop.held = true;
    // three redraws the scene once per tick of its own animation loop: asked
    // for the picture twice in one tick, it redraws only the final full-screen
    // quad, and a loop of hand-made frames times almost nothing. So the tick
    // is advanced by hand, as the animation loop would do it.
    const cpu: number[] = [];
    const total: number[] = [];
    for (let i = 0; i < count + 20; i++) {
      const start = performance.now();
      drawByHand(renderer, engine.draw);
      const handed = performance.now();
      await gpuDone(renderer);
      if (i >= 20) {
        cpu.push(handed - start);
        total.push(performance.now() - start);
      }
    }
    const info = renderer.info.render as unknown as { drawCalls: number; triangles: number };
    loop.held = false;
    return { cpu: median(cpu), total: median(total), draws: info.drawCalls, triangles: info.triangles };
  }

  /**
   * How much the picture changes from frame to frame with the camera still:
   * draws `count` frames and, for each pixel, takes the spread between its
   * darkest and lightest value. A steady picture gives nearly nothing; one
   * that shimmers or crawls gives a lot. `show` lays the spread over the
   * page (white = changing) until the next call with `show` false.
   */
  async function flicker(count = 16, show = false): Promise<{ mean: number; busy: number }> {
    loop.held = true;
    const width = Math.round(canvas.width / 2);
    const height = Math.round(canvas.height / 2);
    const copy = document.createElement("canvas");
    copy.width = width;
    copy.height = height;
    const context = copy.getContext("2d", { willReadFrequently: true })!;
    const low = new Uint8Array(width * height).fill(255);
    const high = new Uint8Array(width * height);
    for (let i = 0; i < count + 30; i++) {
      drawByHand(renderer, engine.draw);
      if (i >= 30) {
        context.drawImage(canvas, 0, 0, width, height);
        const pixels = context.getImageData(0, 0, width, height).data;
        for (let p = 0, at = 0; p < low.length; p++, at += 4) {
          const light = (pixels[at]! * 3 + pixels[at + 1]! * 6 + pixels[at + 2]!) / 10;
          if (light < low[p]!) low[p] = light;
          if (light > high[p]!) high[p] = light;
        }
      }
      await gpuDone(renderer);
    }
    let sum = 0;
    let busy = 0;
    const image = context.createImageData(width, height);
    for (let p = 0; p < low.length; p++) {
      const spread = high[p]! - low[p]!;
      sum += spread;
      if (spread > 20) busy++;
      const shown = Math.min(255, spread * 5);
      image.data[p * 4] = image.data[p * 4 + 1] = image.data[p * 4 + 2] = shown;
      image.data[p * 4 + 3] = 255;
    }
    document.querySelector("#flicker")?.remove();
    if (show) {
      context.putImageData(image, 0, 0);
      copy.id = "flicker";
      copy.style.cssText = "position:fixed;inset:0;width:100%;height:100%;z-index:3";
      document.body.appendChild(copy);
    }
    loop.held = false;
    return { mean: +(sum / low.length).toFixed(2), busy: +((100 * busy) / low.length).toFixed(2) };
  }

  /**
   * Flies the camera through `points` at `speed` m/s, a sixtieth of a second
   * of travel per frame, and times every frame with the GPU's share and
   * whatever arrived and was set up in between. What matters is the worst
   * frames: that is the stutter a player would feel as the map streams.
   */
  async function run(points: [number, number, number][], speed = 12): Promise<Record<string, unknown>> {
    loop.held = true;
    hooks.takeCamera();
    // Always the same number of pixels, whatever size the window happens to be: runs are only comparable so.
    engine.viewport.pin(1280, 800);
    perf.clear();
    perf.recording = true;
    const times: number[] = [];
    /** For each frame: streaming, drawing (CPU), waiting for the GPU, and what arrived in between. */
    const parts: [number, number, number, number][] = [];
    const from = new THREE.Vector3();
    const to = new THREE.Vector3();
    for (let leg = 0; leg + 1 < points.length; leg++) {
      from.fromArray(points[leg]!);
      to.fromArray(points[leg + 1]!);
      const steps = Math.max(1, Math.round((from.distanceTo(to) / speed) * 60));
      for (let step = 0; step < steps; step++) {
        const start = performance.now();
        camera.position.lerpVectors(from, to, step / steps);
        camera.lookAt(to.x, camera.position.y - 1, to.z);
        hooks.advance(1 / 60, camera.position);
        const streamed = performance.now();
        drawByHand(renderer, engine.draw);
        const drawn = performance.now();
        await gpuDone(renderer);
        const finished = performance.now();
        await new Promise((resolve) => setTimeout(resolve)); // lets what has arrived be dealt with, as between real frames
        const end = performance.now();
        times.push(end - start);
        parts.push([streamed - start, drawn - streamed, finished - drawn, end - finished]);
      }
    }
    perf.recording = false;
    loop.held = false;
    const sorted = [...times].sort((a, b) => a - b);
    const at = (share: number): number => +sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * share))]!.toFixed(1);
    return {
      frames: times.length,
      median: at(0.5),
      p95: at(0.95),
      p99: at(0.99),
      worst: +sorted[sorted.length - 1]!.toFixed(1),
      over33: times.filter((ms) => ms > 33.3).length,
      over50: times.filter((ms) => ms > 50).length,
      work: perf.totals(),
      slowest: perf.slowest(6).map((entry) => `${entry.label} ${entry.ms.toFixed(1)}`),
      // The eight worst frames: total = streaming + drawing + GPU + arrivals.
      // Twenty frames at a time: the mean of drawing and of the GPU, to see when it is slow and for how long.
      series: Array.from({ length: Math.ceil(parts.length / 20) }, (_, bucket) => {
        const slice = parts.slice(bucket * 20, bucket * 20 + 20);
        const mean = (column: number): number => Math.round(slice.reduce((sum, part) => sum + part[column]!, 0) / slice.length);
        return `${mean(1)}/${mean(2)}`;
      }).join(" "),
      worstFrames: times
        .map((ms, i) => ({ ms, i }))
        .sort((a, b) => b.ms - a.ms)
        .slice(0, 8)
        .map(({ ms, i }) => `#${i} ${ms.toFixed(0)} = ${parts[i]!.map((part) => part.toFixed(0)).join(" + ")}`),
    };
  }

  return { pace, tune, measure, flicker, run };
}
