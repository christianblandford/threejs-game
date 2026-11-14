/**
 * A stopwatch for the work that happens now and then rather than every frame:
 * a chunk arriving, grass being planted, the walkable area being rebuilt.
 * That is the work that makes a game stutter, and an average frame time
 * never shows it.
 *
 * `timed("chunk set-up", () => ...)` runs the function and, when recording is
 * on, notes how long it took. Recording is off unless something turns it on
 * (`perf.recording = true` from the console, or the debug panel), so in
 * normal play this costs one comparison per call.
 *
 * From the console in a dev build: `perf.recording = true`, play, then
 * `perf.slowest()` for the worst single pieces of work and `perf.totals()`
 * for where the time went overall.
 */
export interface PerfEntry {
  label: string;
  /** Milliseconds it took. */
  ms: number;
  /** When it started (performance.now()). */
  at: number;
}

const entries: PerfEntry[] = [];
const LIMIT = 5000;

export const perf = {
  recording: false,
  entries,
  clear(): void {
    entries.length = 0;
  },
  /** The longest single pieces of work recorded. */
  slowest(count = 15): PerfEntry[] {
    return [...entries].sort((a, b) => b.ms - a.ms).slice(0, count);
  },
  /** Total and worst time per kind of work. */
  totals(): Record<string, { count: number; total: number; worst: number }> {
    const result: Record<string, { count: number; total: number; worst: number }> = {};
    for (const entry of entries) {
      const row = (result[entry.label] ??= { count: 0, total: 0, worst: 0 });
      row.count++;
      row.total += entry.ms;
      row.worst = Math.max(row.worst, entry.ms);
    }
    return result;
  },
};

export function timed<T>(label: string, work: () => T): T {
  if (!perf.recording) return work();
  const at = performance.now();
  try {
    return work();
  } finally {
    const ms = performance.now() - at;
    // Work that took no time is not what anyone is looking for, and there is a lot of it.
    if (ms >= 0.2) {
      if (entries.length >= LIMIT) entries.splice(0, LIMIT / 2);
      entries.push({ label, ms, at });
    }
  }
}

if (import.meta.env.DEV) (globalThis as unknown as { perf: typeof perf }).perf = perf;
