import type * as THREE from "three/webgpu";
import type { NavigationSources } from "./sources";

/** The navigation's settings, and what it gives back: itself, and the agents that walk on it. */

export interface NavigationOptions {
  /** How close an agent's centre may get to walls and drops, in metres. */
  agentRadius?: number;
  /** Agents fit under anything at least this far above the ground. */
  agentHeight?: number;
  /** Tallest ledge walked up without jumping. */
  maxStep?: number;
  /** Steepest walkable slope, in degrees. */
  maxSlope?: number;
  /** Horizontal resolution of the navmesh, in metres. Half the agent radius is the usual choice. */
  cellSize?: number;
  /** Side of one navmesh tile, in metres. A tile is built in one go, so smaller tiles keep each frame's share short. */
  tileSize?: number;
  /** Milliseconds per frame given to building tiles. The last tile started may run a millisecond or two over. */
  frameBudget?: number;
  /** Capacity of the crowd. */
  maxAgents?: number;
}

export const DEFAULT_NAVIGATION_OPTIONS: Readonly<Required<NavigationOptions>> = {
  agentRadius: 0.25,
  agentHeight: 0.6,
  maxStep: 0.18,
  maxSlope: 45,
  cellSize: 0.125,
  tileSize: 6,
  frameBudget: 1.5,
  maxAgents: 64,
};

export interface NavAgentOptions {
  /** Top speed in m/s. */
  speed?: number;
  /** How quickly it reaches that speed, in m/s². */
  acceleration?: number;
  radius?: number;
  height?: number;
}

/** A character driven along the navmesh. Position your model from `position` each frame. */
export interface NavAgent {
  /**
   * The agent's feet, on the navmesh. The navmesh floats a few centimetres
   * above the real ground: for a model to stand exactly on it, cast a short
   * ray down through this point, as npcs.ts does.
   */
  readonly position: THREE.Vector3;
  /** Metres per second. Zero when standing. */
  readonly velocity: THREE.Vector3;
  speed: number;
  /** Walks to the navmesh point nearest `target`. False if there is no navmesh near it. */
  goTo(target: THREE.Vector3): boolean;
  stop(): void;
  /** Moves instantly, dropping any destination. */
  teleport(position: THREE.Vector3): void;
  remove(): void;
}

export interface Navigation {
  /** True once the first build has finished. */
  readonly ready: boolean;
  /** Called each time the navmesh has caught up with the latest `rebuild`. */
  onRebuilt: (() => void) | null;
  /**
   * Makes the navmesh match `sources`. Only tiles whose geometry changed are
   * rebuilt, a few per frame and nearest the camera first, so call it
   * whenever the world changes; calls in quick succession are merged.
   * Resolves when the navmesh is up to date.
   */
  rebuild(sources: NavigationSources): Promise<void>;
  findPath(from: THREE.Vector3, to: THREE.Vector3): THREE.Vector3[];
  /** The navmesh point nearest `position`, or null if there is none within a couple of metres. */
  closestPoint(position: THREE.Vector3): THREE.Vector3 | null;
  /** A random point that can be walked to from `center`, roughly within `radius`. */
  randomPointAround(center: THREE.Vector3, radius: number): THREE.Vector3 | null;
  /** Adds an agent at the navmesh point nearest `start`. Works before the navmesh exists: the agent waits. */
  addAgent(start: THREE.Vector3, options?: NavAgentOptions): NavAgent;
  /** Draws the navmesh into `scene` (or stops). For debugging: redrawing it after each rebuild is slow. */
  showDebug(scene: THREE.Scene | null): void;
  /** Once a frame: builds a little, and moves the agents by `dt` seconds. `eye` decides what is built first. */
  update(dt: number, eye: THREE.Vector3): void;
  readonly stats: { tiles: number; queued: number };
  /** True while a rebuild is waiting or under way: the walkable world is not yet what it will be. */
  readonly busy: boolean;
  /** Resolves once the walkable world is what it will be: gets on with any rebuild, whether or not frames are being drawn. */
  settled(): Promise<void>;
  /** Seeds the random points it hands out (`randomPointAround`), so that a run can be played again. */
  reseed(seed: number): void;
  /** Where that sequence of random points has got to: save it, and set it to carry on from there. */
  randomState: number;
}
