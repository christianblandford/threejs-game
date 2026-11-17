import { getSettings, QUALITY, type QualityLevel } from "./settings";

/**
 * The page's `?name=value` switches: how a setting is tried without a
 * rebuild (`?gi=0`, `?exposure=0.9`, `?webgl`). Read where they are used,
 * with the default beside them, so each file says what it can be told.
 *
 * It is one object for the whole page and it can be written to:
 * `bench.tune` sets a switch in the running page and builds the picture
 * again, which is how two settings are timed back to back.
 */
export const params = new URLSearchParams(location.search);

/** A switch that is on unless the address says `=0` (or off unless it says otherwise, with `otherwise` false). */
export const switchedOn = (name: string, otherwise = true): boolean => (params.has(name) ? params.get(name) !== "0" : otherwise);

/** A number from the address, or the default. */
export const numberOf = (name: string, otherwise: number): number => Number(params.get(name) ?? otherwise);

/** The quality in force: `?quality=` wins over the saved setting. */
export function activeQuality(): QualityLevel {
  const asked = params.get("quality");
  return asked && asked in QUALITY ? (asked as QualityLevel) : getSettings().quality;
}
