import * as THREE from "three/webgpu";
import type { MapManifest } from "./map";

/**
 * Things the player can use with the interact key: placed in Blender as
 * Empties with a custom property (`interact`, `door`, `talk`) and optionally
 * `prompt` and `radius`. What using one does is decided by whoever listens
 * for its kind: this file only works out which one, if any, the player is
 * standing at and looking toward, and says when it is used.
 *
 * And zones: an Empty with `trigger = "<name>"` is an area, the size and
 * shape the Empty is drawn (a box, turned as it is turned, or a sphere),
 * and whoever listens for its name is told when the player walks in or
 * out. A door with `walk = true` is a zone too: walking in uses it. Only
 * the player is watched (the Babylon project has the physics engine report
 * any body).
 */

export type Marker = MapManifest["markers"][number];

export interface Usable {
  /** Which property made it usable: "interact", "door" or "talk". */
  kind: string;
  /** The property's value: the name of the thing, the door's destination, the story. */
  name: string;
  position: THREE.Vector3;
  prompt: string;
  radius: number;
  enabled: boolean;
  /** The id of the place it stands in. */
  space: string;
  marker: Marker;
}

/** An area that tells when the player walks in or out. */
export interface Zone {
  name: string;
  space: string;
  centre: THREE.Vector3;
  /** Half its size along its own axes; for a sphere, x is the radius. */
  half: THREE.Vector3;
  sphere: boolean;
  /** The turn about the vertical, as cosine and sine. */
  turn: [number, number];
  inside: boolean;
  marker: Marker;
  /** For a door that is walked through: what entering uses. */
  door: Usable | null;
}

export interface Interact {
  /** Listens for the player walking into (or out of) the zones of a name. */
  onEnter(name: string, handler: (zone: Zone) => void): void;
  onLeave(name: string, handler: (zone: Zone) => void): void;
  readonly zones: readonly Zone[];
  /** Makes the usable things placed in a map known. `toThree` turns a marker's position into this scene's. */
  followMap(space: string, markers: Marker[], toThree: (marker: Marker) => THREE.Vector3): void;
  on(kind: string, handler: (used: Usable) => void): void;
  all(kind: string, name?: string): Usable[];
  readonly focus: Usable | null;
  /** True: nothing can be used for now (a conversation has the key). */
  suspended: boolean;
  /** Once a frame: finds what is in reach, and uses it if the key went down. */
  update(feet: THREE.Vector3, camera: THREE.Camera, currentSpace: string, pressed: boolean): void;
  onFocus: ((usable: Usable | null) => void) | null;
}

const DEFAULTS: Record<string, { prompt: string; radius: number }> = {
  interact: { prompt: "Use", radius: 2 },
  door: { prompt: "Enter", radius: 2.2 },
  talk: { prompt: "Talk", radius: 2.4 },
};

export function createInteract(): Interact {
  const usables: Usable[] = [];
  const zones: Zone[] = [];
  const entering = new Map<string, ((zone: Zone) => void)[]>();
  const leaving = new Map<string, ((zone: Zone) => void)[]>();
  const handlers = new Map<string, ((used: Usable) => void)[]>();
  const local = new THREE.Vector3();
  /** Whether a point (the player's middle) is in a zone. */
  function within(zone: Zone, point: THREE.Vector3): boolean {
    local.copy(point).sub(zone.centre);
    if (zone.sphere) return local.lengthSq() <= zone.half.x * zone.half.x;
    const [cos, sin] = zone.turn;
    const x = local.x * cos + local.z * sin;
    const z = -local.x * sin + local.z * cos;
    return Math.abs(x) <= zone.half.x && Math.abs(local.y) <= zone.half.y && Math.abs(z) <= zone.half.z;
  }
  const zoneFor = (space: string, marker: Marker, name: string, centre: THREE.Vector3, fallback: [number, number, number], door: Usable | null): Zone => {
    const [x, y, z] = marker.size ?? fallback;
    // A turn of `yaw` in the map's left-handed space is the opposite turn here.
    return { name, space, centre, half: new THREE.Vector3(x / 2, y / 2, z / 2), sphere: marker.shape === "sphere", turn: [Math.cos(-marker.yaw), Math.sin(-marker.yaw)], inside: false, marker, door };
  };
  let focus: Usable | null = null;
  const view = new THREE.Vector3();
  const middle = new THREE.Vector3();
  const toward = new THREE.Vector3();

  const api: Interact = {
    followMap(space, markers, toThree) {
      for (const marker of markers) {
        if (typeof marker.props.trigger === "string") zones.push(zoneFor(space, marker, marker.props.trigger, toThree(marker), [2, 2, 2], null));
        for (const kind of Object.keys(DEFAULTS)) {
          const name = marker.props[kind];
          if (typeof name !== "string") continue;
          const walkedInto = kind === "door" && (marker.props.walk === true || marker.props.walk === 1);
          usables.push({
            kind,
            name,
            position: toThree(marker),
            prompt: typeof marker.props.prompt === "string" ? marker.props.prompt : DEFAULTS[kind]!.prompt,
            radius: typeof marker.props.radius === "number" ? marker.props.radius : DEFAULTS[kind]!.radius,
            // A door that is walked through has no prompt: it is never the thing in reach.
            enabled: true,
            space,
            marker,
          });
          if (walkedInto) {
            const door = usables[usables.length - 1]!;
            door.radius = 0;
            zones.push(zoneFor(space, marker, "door", toThree(marker), [2, 2.4, 2], door));
          }
        }
      }
    },
    onEnter(name, handler) {
      entering.set(name, [...(entering.get(name) ?? []), handler]);
    },
    onLeave(name, handler) {
      leaving.set(name, [...(leaving.get(name) ?? []), handler]);
    },
    zones,
    on(kind, handler) {
      handlers.set(kind, [...(handlers.get(kind) ?? []), handler]);
    },
    all: (kind, name) => usables.filter((usable) => usable.kind === kind && (name === undefined || usable.name === name)),
    get focus() {
      return focus;
    },
    suspended: false,
    onFocus: null,
    update(feet, camera, currentSpace, pressed) {
      // Zones: where the player's middle is, against each zone of the place they are in.
      if (!api.suspended) {
        middle.copy(feet).y += 0.3;
        for (const zone of zones) {
          const inside = zone.space === currentSpace && within(zone, middle);
          if (inside === zone.inside) continue;
          zone.inside = inside;
          if (inside && zone.door) {
            if (zone.door.enabled) for (const handler of handlers.get("door") ?? []) handler(zone.door);
          } else for (const handler of (inside ? entering : leaving).get(zone.name) ?? []) handler(zone);
        }
      }
      let best: Usable | null = null;
      if (!api.suspended) {
        let nearest = Infinity;
        camera.getWorldDirection(view);
        for (const usable of usables) {
          if (!usable.enabled || usable.space !== currentSpace) continue;
          const distance = Math.hypot(usable.position.x - feet.x, usable.position.z - feet.z);
          if (distance > usable.radius || Math.abs(usable.position.y - feet.y) > 3) continue;
          // Roughly in front of the camera: not something behind the player's back.
          toward.copy(usable.position).sub(camera.position).normalize();
          if (toward.dot(view) < 0.2) continue;
          if (distance < nearest) {
            nearest = distance;
            best = usable;
          }
        }
      }
      if (best !== focus) {
        focus = best;
        api.onFocus?.(focus);
      }
      if (focus && pressed) for (const handler of handlers.get(focus.kind) ?? []) handler(focus);
    },
  };
  return api;
}
