import * as THREE from "three/webgpu";

/**
 * The layer a chunk's shadow-only mesh is on. The cameras that draw the
 * shadow maps must see it (`camera.layers.enable(SHADOW_LAYER)`); the
 * player's camera must not, and by default does not.
 */
export const SHADOW_LAYER = 1;
/** A pooled prop smaller than this (m, the radius of the ball around it) casts no shadow: a sign's bracket, a lamp's glass. */
export const SMALLEST_CASTER = 0.5;
let shadowOnly: THREE.Material | null = null;

/**
 * One mesh holding every solid shadow caster of a chunk, drawn only into
 * the shadow maps.
 *
 * A chunk's buildings arrive as five or six meshes, one per material, and
 * each was drawn again into every shadow cascade that reaches it: 750 of
 * the frame's 1,000 draw calls were shadows, 8 ms of script a frame. A
 * shadow map holds depth and nothing else, so what a thing is made of does
 * not matter to it. Merged, a chunk is one draw per cascade.
 */
export function* shadowProxy(scene: THREE.Group, casters: THREE.Mesh[]): Generator<void, THREE.Mesh | null> {
  let vertexCount = 0;
  let indexCount = 0;
  for (const mesh of casters) {
    const position = mesh.geometry.attributes.position;
    if (!position) continue;
    vertexCount += position.count;
    indexCount += mesh.geometry.index ? mesh.geometry.index.count : position.count;
  }
  if (indexCount === 0) return null;
  const positions = new Float32Array(vertexCount * 3);
  const indices = new Uint32Array(indexCount);
  const toScene = new THREE.Matrix4().copy(scene.matrixWorld).invert();
  const placed = new THREE.Matrix4();
  const point = new THREE.Vector3();
  let vertex = 0;
  let index = 0;
  for (const mesh of casters) {
    const position = mesh.geometry.attributes.position as THREE.BufferAttribute | undefined;
    if (!position) continue;
    placed.multiplyMatrices(toScene, mesh.matrixWorld);
    for (let i = 0; i < position.count; i++) {
      point.fromBufferAttribute(position, i).applyMatrix4(placed);
      positions[(vertex + i) * 3] = point.x;
      positions[(vertex + i) * 3 + 1] = point.y;
      positions[(vertex + i) * 3 + 2] = point.z;
    }
    const own = mesh.geometry.index;
    if (own) for (let i = 0; i < own.count; i++) indices[index++] = own.getX(i) + vertex;
    else for (let i = 0; i < position.count; i++) indices[index++] = vertex + i;
    vertex += position.count;
    yield;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  geometry.computeBoundingSphere();
  geometry.computeBoundingBox();
  // Both faces: some of what is merged was mirrored into place, and depth does not care which way a triangle is wound.
  shadowOnly ??= new THREE.MeshBasicNodeMaterial({ side: THREE.DoubleSide });
  const proxy = new THREE.Mesh(geometry, shadowOnly);
  proxy.name = "shadow";
  proxy.castShadow = true;
  proxy.receiveShadow = false;
  proxy.matrixAutoUpdate = false;
  proxy.layers.set(SHADOW_LAYER);
  return proxy;
}
