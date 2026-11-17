import * as THREE from "three/webgpu";
import { GLTFLoader, type GLTFParser } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { upgradeMaterial, type Blend, type MaterialExtras } from "../materials";
import { waterMaterial, type WaterDepth } from "../water";
import type { MapManifest } from "./manifest";

/**
 * The loader for a map's files, set up so that every file shares the map's
 * materials.
 *
 * Every chunk file names the same materials and the same texture files.
 * There is one material per name for the whole map, so each texture is
 * fetched, decoded and uploaded once and not once per chunk; and each is
 * given what glTF could not say about it (wind, worn paths, water) as it
 * first arrives.
 *
 * @param overrides Material properties by material name, laid over what
 *   the map's own files say.
 */
export async function createMapLoader(
  renderer: THREE.WebGPURenderer,
  manifest: MapManifest,
  baseUrl: string,
  origin: THREE.Vector3,
  overrides: Record<string, MaterialExtras> | undefined,
): Promise<{ loader: GLTFLoader; materials: Map<string, Promise<THREE.Material>> }> {
  const textures = new THREE.TextureLoader();

  // The worn-path masks, and the water's ripples.
  const blends = new Map<string, Blend>();
  for (const blend of manifest.blends ?? []) {
    const mask = await textures.loadAsync(baseUrl + blend.mask);
    mask.colorSpace = THREE.NoColorSpace;
    mask.wrapS = mask.wrapT = THREE.ClampToEdgeWrapping;
    blends.set(blend.material, { material: blend.material, layer: blend.layer, mask, map: blend.map });
  }
  const ripples = await textures.loadAsync("/textures/waternormals.jpg");
  // With the terrain's height image, water knows how deep it is and thins out toward the bank.
  let depth: WaterDepth | null = null;
  if (manifest.heights) {
    const heights = await textures.loadAsync(baseUrl + manifest.heights.file);
    heights.colorSpace = THREE.NoColorSpace;
    heights.minFilter = heights.magFilter = THREE.NearestFilter; // two bytes of one number: not to be blended
    heights.generateMipmaps = false;
    depth = { heights, ...manifest.heights, origin };
  }
  const water = waterMaterial(ripples, false, depth);
  const sea = waterMaterial(ripples, true, depth);
  // Marked, because the loader hands a mesh a copy of its material when the mesh has, say, vertex colours:
  // comparing with these two would then miss it, and the water would be given a collider to stand on.
  water.userData.water = sea.userData.water = true;

  const ktx2 = new KTX2Loader().setTranscoderPath("/basis/").detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx2);
  // Every chunk file names the same materials and the same texture files. One
  // material per name for the whole map, so each texture is fetched, decoded
  // and uploaded once and not once per chunk; and each is given what glTF
  // could not say about it (wind, worn paths, water) as it first arrives.
  const materials = new Map<string, Promise<THREE.Material>>();
  loader.register((parser: GLTFParser) => ({
    name: "MAP_shared_materials",
    loadMaterial(index: number) {
      const definition = parser.json.materials?.[index] as { name?: string; extras?: MaterialExtras } | undefined;
      const name = definition?.name;
      if (!name) return null;
      let shared = materials.get(name);
      if (!shared) {
        if (name === "Water" || name === "Sea") shared = Promise.resolve(name === "Sea" ? sea : water);
        else {
          const blend = blends.get(name) ?? null;
          shared = parser.loadMaterial(index).then(async (material) => {
            // A blended-in layer is another of the map's materials, sent ahead in the always-loaded file.
            const layer = blend ? ((await materials.get(blend.layer)) as THREE.MeshStandardMaterial | undefined) ?? null : null;
            return upgradeMaterial(material, { ...definition.extras, ...overrides?.[name] }, blend, layer);
          });
        }
        materials.set(name, shared);
      }
      return shared;
    },
  }));
  return { loader, materials };
}
