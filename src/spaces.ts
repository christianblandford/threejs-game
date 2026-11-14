import * as THREE from "three/webgpu";
import type { Interact, Marker } from "./interact";
import type { LoadedMap } from "./map";

/**
 * Places, and the doors between them. A port of the Babylon project's
 * src/engine/spaces.ts: the same Blender conventions, the same behaviour.
 *
 * Each place is a **space**: a map plus the light it is seen in. Spaces are
 * joined by **doors**, Empties in Blender with a custom property
 *
 *     door = "hall#entrance"      (the space's id, then a marker in it)
 *
 * and optionally `prompt` and `radius`. `door = "back"` leads to wherever
 * the player came into this space from.
 *
 * An interior is loaded into the scene that is already running, beside the
 * level (a couple of hundred metres past its edge), and the player is moved
 * there behind a short fade. The place left behind is not unloaded: it is
 * hidden and its streaming is held where it was, so walking back out is the
 * same short fade. Nothing that takes seconds (the renderer, physics, the
 * sky, the player, compiled shaders) is rebuilt.
 *
 * What a space looks like inside is its `Environment`: shares of the level's
 * own sun, sky light and haze, and an exposure. Indoors the sun stays on
 * (the room's own walls and roof shade it, and it comes through the windows)
 * and the sky's ambient light is turned down, since nothing shades that.
 */

export interface Environment {
  /** Sunlight, as a share of the level's own. */
  sun: number;
  /** The sky's ambient light and reflections, as a share of the level's own. */
  ambient: number;
  /** Distance haze, as a share of the level's own. 0 turns it off. */
  fog: number;
  /** Exposure, as a multiple of the level's own: above 1 in a dim room, as eyes adjust. */
  exposure: number;
  /** How far from the camera shadows are drawn (m). Short in a room: the same shadow maps then cover less, and are sharper. */
  shadowDistance?: number;
}

export const OUTDOORS: Environment = { sun: 1, ambient: 1, fog: 1, exposure: 1 };
export const INDOORS: Environment = { sun: 1, ambient: 0.35, fog: 0, exposure: 1.27, shadowDistance: 45 };

export interface SpaceInfo {
  id: string;
  map: string;
  environment?: Partial<Environment>;
}

export interface Arrival {
  position: THREE.Vector3;
  yaw: number;
}

export interface SpacesOptions {
  /** The level the scene was built around, already loaded. */
  home: { id: string; map: LoadedMap };
  interact: Interact;
  /** Looks an interior up by id. */
  find(id: string): SpaceInfo | undefined;
  /** Loads an interior's map with its zero at `origin`, and adds it to the scene. */
  load(space: SpaceInfo, origin: THREE.Vector3): Promise<LoadedMap>;
  /** Called for every map as it becomes part of the world (the level at once, an interior once loaded). */
  attach?(id: string, map: LoadedMap): void;
  /** Sets the light: called with what the current space asks for. */
  light(environment: Environment): void;
  /** Puts the player at a spot, facing `yaw`. */
  place(position: THREE.Vector3, yaw: number): void;
  /** Where the player is and which way they look. */
  whereabouts(): Arrival;
  /** Start loading an interior when the player is this close to a door into it (m). Default 30. */
  preloadDistance?: number;
}

export interface Spaces {
  /** The id of the space the player is in. */
  readonly current: string;
  /** True from the moment a door is used until the player can move again. */
  readonly travelling: boolean;
  /**
   * Goes to "space#marker" (or "space", for its `spawn` marker; or "back").
   * With `at`, the player is put there instead of at a marker (restoring a
   * saved game); `at.position` is relative to the place's own zero.
   */
  travel(destination: string, at?: Arrival): Promise<void>;
  preload(id: string): Promise<void>;
  map(id: string): LoadedMap | null;
  /** Where a loaded space's own zero is in the scene. A saved position is kept relative to this. */
  origin(id: string): THREE.Vector3;
  /** Called on arrival. */
  onTravelled: ((trip: { from: string; to: string }) => void) | null;
  /** How long things took (ms). */
  readonly timings: { loads: Record<string, number>; lastTrip: { from: string; to: string; ms: number } | null };
  /** Once a frame, with where the camera is: streams the current map, and starts loading what is behind a nearby door. */
  update(eye: THREE.Vector3): void;
}

/**
 * Where interiors are put: this far past the level's edge, a row of plots
 * this wide. Near, because a position is stored in 32 bits and gets coarser
 * with distance from zero (at 2 km, surfaces a few millimetres apart
 * flicker); far enough that nothing of one place reaches the other.
 */
const POCKET_GAP = 192;
const POCKET_PLOT = 256;
/** Fade to black and back (ms, each way). */
const FADE = 220;
/** Frames drawn behind the black before it lifts, so first-sight shader compiles are not seen. */
const SETTLE_FRAMES = 4;

interface Loaded {
  id: string;
  map: LoadedMap;
  origin: THREE.Vector3;
  environment: Environment;
  cameFrom: ({ space: string } & Arrival) | null;
}

/** A marker's place in three's space, and the heading a character there faces (the files speak a left-handed space). */
export const markerPosition = (marker: Marker): THREE.Vector3 => new THREE.Vector3(-marker.position[0], marker.position[1], marker.position[2]);
export const markerYaw = (marker: Marker): number => Math.PI - marker.yaw;

export function createSpaces(options: SpacesOptions): Spaces {
  const { interact } = options;
  const preloadDistance = options.preloadDistance ?? 30;
  const timings: Spaces["timings"] = { loads: {}, lastTrip: null };
  const spaces = new Map<string, Loaded>();
  const loading = new Map<string, Promise<Loaded>>();
  /** Interiors being set up ahead of need, and those somebody is waiting for. */
  const arriving = new Map<string, LoadedMap>();
  const hurrying = new Set<string>();
  const home: Loaded = { id: options.home.id, map: options.home.map, origin: new THREE.Vector3(), environment: OUTDOORS, cameFrom: null };
  spaces.set(home.id, home);
  let current = home.id;
  let travelling = false;
  let pockets = 0;
  // Beyond the middle of the level's edge (its eastern one in Blender, which is -x here).
  const bounds = home.map.manifest;
  const edge = new THREE.Vector3(-bounds.max[0], 0, (bounds.min[2] + bounds.max[2]) / 2);

  // --- Doors -------------------------------------------------------------------

  const known = (id: string): boolean => id === home.id || options.find(id) !== undefined;
  function attach(space: Loaded): void {
    interact.followMap(space.id, space.map.manifest.markers, markerPosition);
    for (const door of interact.all("door")) {
      if (door.space !== space.id || door.name === "back") continue;
      const [id, markerName = "spawn"] = door.name.split("#") as [string, string | undefined];
      if (!known(id)) {
        // A door to somewhere this game does not have: say so once, and leave it shut.
        console.info(`Door "${door.marker.name}" leads to "${door.name}", a place that is not in this game: left shut.`);
        door.enabled = false;
      } else if (import.meta.env.DEV) void check(space.id, door.marker.name, id, markerName);
    }
    options.attach?.(space.id, space.map);
  }
  /** In development, says so at once if a door leads to a marker that does not exist. */
  async function check(space: string, door: string, id: string, markerName: string): Promise<void> {
    try {
      const info = options.find(id);
      const markers = info ? ((await (await fetch(info.map)).json()) as { markers: Marker[] }).markers : home.map.manifest.markers;
      if (!markers.some((marker) => marker.name === markerName)) console.warn(`Door "${door}" in "${space}" leads to "${id}#${markerName}", but "${id}" has no marker called "${markerName}".`);
    } catch {
      console.warn(`Door "${door}" in "${space}" leads to "${id}", but its map could not be read (not exported yet?).`);
    }
  }
  interact.on("door", (door) => void travel(door.name).catch((error) => console.error(`Door "${door.marker.name}" leads nowhere:`, error)));

  // --- Loading interiors -------------------------------------------------------

  function load(id: string): Promise<Loaded> {
    const have = spaces.get(id);
    if (have) return Promise.resolve(have);
    let pending = loading.get(id);
    if (!pending) {
      const info = options.find(id);
      if (!info) return Promise.reject(new Error(`No space called "${id}".`));
      // Plots in a row along the edge, working outward from its middle: 0, +1, -1, +2...
      const plot = pockets++;
      const along = Math.ceil(plot / 2) * (plot % 2 === 1 ? 1 : -1);
      const origin = new THREE.Vector3(edge.x - POCKET_GAP, edge.y, edge.z + along * POCKET_PLOT);
      const started = performance.now();
      pending = options.load(info, origin).then(async (map) => {
        // A map streams around the camera, and the camera is elsewhere: have
        // this one stream around its own middle until somebody walks in.
        const { min, max } = map.manifest;
        map.shown = false;
        map.hold(new THREE.Vector3(-(min[0] + max[0]) / 2, min[1], (min[2] + max[2]) / 2));
        // Loaded ahead of need, while the game plays: at the per-frame pace,
        // unless somebody is already at the door (`travel` hurries it).
        arriving.set(id, map);
        await map.settled(hurrying.has(id));
        arriving.delete(id);
        const loaded: Loaded = { id, map, origin, environment: { ...INDOORS, ...info.environment }, cameFrom: null };
        spaces.set(id, loaded);
        loading.delete(id);
        attach(loaded);
        timings.loads[id] = Math.round(performance.now() - started);
        return loaded;
      });
      pending.catch(() => loading.delete(id));
      loading.set(id, pending);
    }
    return pending;
  }

  // --- The trip ----------------------------------------------------------------

  // A black sheet over the canvas. Plain HTML: it covers the picture whatever the renderer is doing.
  const sheet = document.createElement("div");
  sheet.style.cssText = `position:fixed;inset:0;background:#000;opacity:0;pointer-events:none;transition:opacity ${FADE}ms ease;z-index:5`;
  document.body.appendChild(sheet);
  const fade = (to: 0 | 1): Promise<void> => {
    sheet.style.opacity = String(to);
    return new Promise((resolve) => setTimeout(resolve, FADE + 30));
  };
  let waiting: { left: number; resolve(): void }[] = [];
  const frames = (count: number): Promise<void> =>
    new Promise((resolve) => {
      const entry = { left: count, resolve };
      waiting.push(entry);
      // A page in the background draws no frames: don't wait for ever on one.
      setTimeout(() => {
        waiting = waiting.filter((other) => other !== entry);
        resolve();
      }, 1500);
    });

  async function travel(destination: string, at?: Arrival): Promise<void> {
    if (travelling) return;
    const here = spaces.get(current)!;
    let id: string;
    let markerName: string | undefined;
    let back: Loaded["cameFrom"] = null;
    if (destination === "back") {
      back = here.cameFrom;
      if (!back) {
        // Nobody walked in (a saved game was restored here): go out by a door that leads in.
        const way = interact.all("door").find((door) => door.space !== current && door.name.split("#")[0] === current);
        if (!way) throw new Error(`"back" from "${current}", but no door leads into it.`);
        back = { space: way.space, position: way.position.clone(), yaw: markerYaw(way.marker) };
      }
      id = back.space;
    } else {
      [id, markerName] = destination.split("#") as [string, string | undefined];
    }
    if (!spaces.has(id) && !options.find(id)) throw new Error(`No space called "${id}".`);

    travelling = true;
    interact.suspended = true;
    try {
      const started = performance.now();
      // Waited for behind the fade: no per-frame pace now.
      hurrying.add(id);
      void arriving.get(id)?.settled();
      const [there] = await Promise.all([load(id), fade(1)]);

      let position: THREE.Vector3;
      let yaw: number;
      if (back) {
        ({ position, yaw } = back);
      } else if (at) {
        position = at.position.clone().add(there.origin);
        yaw = at.yaw;
      } else {
        const marker = there.map.manifest.markers.find((found) => found.name === (markerName ?? "spawn"));
        if (!marker) throw new Error(`"${id}" has no marker called "${markerName ?? "spawn"}".`);
        position = markerPosition(marker);
        yaw = markerYaw(marker);
        // Remember the way back: where the player stands now, turned round.
        const now = options.whereabouts();
        there.cameFrom = { space: current, position: now.position.clone(), yaw: now.yaw + Math.PI };
      }

      // The space being left stays loaded exactly as it is. The one arrived
      // in is held around the arrival point until the camera has got there.
      here.map.hold(options.whereabouts().position);
      there.map.hold(position);
      await there.map.settled();
      // One place is seen at a time.
      there.map.shown = true;
      if (here !== there) here.map.shown = false;
      options.light(there.environment);
      options.place(position, yaw);
      const from = current;
      current = id;

      // A few frames behind the black: anything seen for the first time compiles its shaders now.
      await frames(SETTLE_FRAMES);
      there.map.hold(null); // the camera is with the player again: stream around it
      await frames(2);
      timings.lastTrip = { from, to: id, ms: Math.round(performance.now() - started) };
      api.onTravelled?.({ from, to: id });
    } finally {
      travelling = false;
      interact.suspended = false;
      await fade(0);
    }
  }

  let frame = 0;
  const api: Spaces = {
    get current() {
      return current;
    },
    get travelling() {
      return travelling;
    },
    travel,
    preload: (id) => load(id).then(() => undefined),
    map: (id) => spaces.get(id)?.map ?? null,
    origin: (id) => spaces.get(id)?.origin ?? new THREE.Vector3(),
    onTravelled: null,
    timings,
    update(eye) {
      for (const entry of waiting) if (--entry.left <= 0) entry.resolve();
      waiting = waiting.filter((entry) => entry.left > 0);
      // Every map gets its turn each frame: the one the player is in streams
      // around the camera, a held one takes no notice of where that is, and
      // both get on with whatever set-up they have waiting.
      for (const space of spaces.values()) space.map.update(eye);
      for (const map of arriving.values()) map.update(eye);
      if (frame++ % 12 !== 0) return;
      if (travelling) return;
      // Start loading what is behind a door as the player walks up to it.
      for (const door of interact.all("door")) {
        if (door.space !== current || door.name === "back" || !door.enabled) continue;
        const id = door.name.split("#")[0]!;
        if (spaces.has(id) || loading.has(id) || !options.find(id)) continue;
        if (Math.hypot(eye.x - door.position.x, eye.z - door.position.z) < preloadDistance) void load(id).catch((error) => console.warn(`Could not load "${id}" ahead of time:`, error));
      }
    },
  };
  options.light(home.environment);
  attach(home);
  return api;
}
