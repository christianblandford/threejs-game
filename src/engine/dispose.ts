import type * as THREE from "three/webgpu";

/**
 * Takes an object out of the scene and frees what it holds on the GPU: its
 * geometries, its materials and their textures, and those of everything
 * under it.
 *
 * Only for things that own what they draw with (a character loaded from
 * its own file). A map's meshes share materials and are freed by the map.
 */
export function disposeObject(object: THREE.Object3D): void {
  object.removeFromParent();
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry.dispose();
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) disposeMaterial(material);
  });
}

/** Frees a material and every texture it names. */
export function disposeMaterial(material: THREE.Material): void {
  for (const value of Object.values(material)) {
    if ((value as THREE.Texture | null)?.isTexture) (value as THREE.Texture).dispose();
  }
  material.dispose();
}
