import type * as THREE from "three/webgpu";

/**
 * A map's manifest (`map.json`), as the Babylon scaffold's Blender export
 * writes it: what chunks there are and where, the markers, and the images
 * that go with the terrain.
 *
 * Positions in it are in the Babylon game's left-handed space: negate x to
 * get three's.
 */
export interface MapManifest {
  chunkSize: number;
  min: [number, number, number];
  max: [number, number, number];
  global: { file: string } | null;
  markers: { name: string; position: [number, number, number]; yaw: number; size?: [number, number, number]; shape?: "box" | "sphere"; props: Record<string, unknown> }[];
  blends?: { material: string; layer: string; mask: string; map: [number, number, number, number] }[];
  /** The terrain's height baked into an image, for water to know its depth. */
  heights?: { file: string; size: number; map: [number, number, number, number]; low: number; range: number } | null;
  chunks: { id: string; min: [number, number, number]; max: [number, number, number]; lods: { file: string; triangles: number }[] }[];
}

/**
 * Fetches a manifest. With an `origin` (a second map beside the first: an
 * interior), everything it says in world coordinates is moved with the map.
 */
export async function readManifest(url: string, origin: THREE.Vector3): Promise<{ manifest: MapManifest; baseUrl: string }> {
  const manifest = (await (await fetch(url)).json()) as MapManifest;
  const baseUrl = url.slice(0, url.lastIndexOf("/") + 1);
  if (origin.lengthSq() > 0) {
    // Everything the manifest says in world coordinates moves with the map.
    // (It speaks the Babylon game's left-handed space: x there is -x here.)
    const shift = (point: [number, number, number]): void => {
      point[0] -= origin.x;
      point[1] += origin.y;
      point[2] += origin.z;
    };
    shift(manifest.min);
    shift(manifest.max);
    for (const marker of manifest.markers) shift(marker.position);
    for (const chunk of manifest.chunks) {
      shift(chunk.min);
      shift(chunk.max);
    }
  }
  return { manifest, baseUrl };
}
