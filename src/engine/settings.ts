/**
 * Player preferences, saved in the browser and edited from the settings
 * menu (game/menu.ts). Anything that should follow a setting listens with
 * `onSettingsChanged`.
 */

export type QualityLevel = "low" | "medium" | "high" | "ultra";

/** What a level of graphics quality means here. picture.ts builds the picture from it. */
export interface QualityPreset {
  /** The most device pixels drawn per CSS pixel. */
  pixelRatio: number;
  /** Shade in creases: none, plain ambient occlusion, or light bounced off what is on screen (which shades creases too). */
  shading: "none" | "occlusion" | "bounced";
  /** For bounced light: directions looked in per pixel, and steps along each. */
  slices: number;
  steps: number;
  /** For bounced light: the share of the canvas it is worked out at, each way. */
  bounceScale: number;
  bloom: boolean;
  /** How much grass is planted: 1 is the full sward. Read when the level loads, so a change shows after the next load. */
  grass: number;
}

export const QUALITY: Record<QualityLevel, QualityPreset> = {
  low: { pixelRatio: 1, shading: "none", slices: 0, steps: 0, bounceScale: 0, bloom: false, grass: 0.35 },
  medium: { pixelRatio: 1, shading: "occlusion", slices: 0, steps: 0, bounceScale: 0, bloom: true, grass: 0.6 },
  // One direction of eight steps at 0.4 of the canvas: 4.5 ms cheaper at 2.25 million pixels than two of six at half, and no different to look at or in how much it flickers.
  high: { pixelRatio: 1.25, shading: "bounced", slices: 1, steps: 8, bounceScale: 0.4, bloom: true, grass: 1 },
  ultra: { pixelRatio: 1.5, shading: "bounced", slices: 3, steps: 8, bounceScale: 0.5, bloom: true, grass: 1 },
};
export const QUALITY_LEVELS = Object.keys(QUALITY) as QualityLevel[];

export interface Settings {
  quality: QualityLevel;
  /** 0..1 */
  masterVolume: number;
  /** Multiplier on look speed: 1 is the default feel. */
  lookSensitivity: number;
  invertY: boolean;
  /** Draw fewer pixels when a view is too heavy to hold sixty frames a second (resolution.ts). */
  autoResolution: boolean;
}

const DEFAULTS: Settings = { quality: "high", masterVolume: 0.7, lookSensitivity: 1, invertY: false, autoResolution: true };
const STORAGE_KEY = "three-settings";

function read(): Settings {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Partial<Settings>;
    const merged = { ...DEFAULTS, ...saved };
    if (!(merged.quality in QUALITY)) merged.quality = DEFAULTS.quality;
    return merged;
  } catch {
    return { ...DEFAULTS }; // storage blocked or corrupt: fall back quietly
  }
}

let current = read();
const listeners: ((settings: Settings, changed: Partial<Settings>) => void)[] = [];

export function getSettings(): Readonly<Settings> {
  return current;
}
/** Calls `listener` with the new settings, and what was changed, after every change. */
export function onSettingsChanged(listener: (settings: Settings, changed: Partial<Settings>) => void): void {
  listeners.push(listener);
}
export function updateSettings(patch: Partial<Settings>): void {
  current = { ...current, ...patch };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
  } catch {
    /* storage blocked: the change still applies for this session */
  }
  for (const listener of listeners) listener(current, patch);
}
