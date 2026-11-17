import * as THREE from "three/webgpu";
import { describe, expect, it } from "vitest";
import { createBatches, type Placings } from "./batches";

const at = (x: number): THREE.Matrix4 => new THREE.Matrix4().makeTranslation(x, 0, 0);
const placings = (count: number, casts = true): Placings => ({ matrices: Array.from({ length: count }, (_, i) => at(i)), casts });

function setup() {
  const root = new THREE.Group();
  const batches = createBatches(root);
  const material = new THREE.MeshBasicMaterial();
  batches.library.set("barrel", [new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material)]);
  batches.library.set("lampGlass", [new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.2, 0.2), material)]);
  return { root, batches };
}
const meshOf = (root: THREE.Group, id: string): THREE.InstancedMesh => root.children.find((child) => child.name === `pool:${id}`) as THREE.InstancedMesh;

describe("the pooled props", () => {
  it("draws a mesh once per placing, from every file that places it", () => {
    const { root, batches } = setup();
    batches.stale.add("barrel");
    batches.write(() => [placings(3), undefined, placings(2)]);
    expect(meshOf(root, "barrel").count).toBe(5);
    expect(batches.placings).toBe(5);
    expect(batches.count).toBe(1);
    expect(batches.stale.size).toBe(0);
  });

  it("rewrites a batch in place when it still fits, and makes a new one only when outgrown", () => {
    const { root, batches } = setup();
    batches.stale.add("barrel");
    batches.write(() => [placings(3)]);
    const first = meshOf(root, "barrel");
    batches.stale.add("barrel");
    batches.write(() => [placings(6)]);
    expect(meshOf(root, "barrel")).toBe(first);
    batches.stale.add("barrel");
    batches.write(() => [placings(40)]);
    expect(meshOf(root, "barrel")).not.toBe(first);
    expect(root.children.filter((child) => child.name === "pool:barrel")).toHaveLength(1);
    expect(batches.placings).toBe(40);
  });

  it("hides a mesh nothing places any more", () => {
    const { root, batches } = setup();
    batches.stale.add("barrel");
    batches.write(() => [placings(3)]);
    batches.stale.add("barrel");
    batches.write(() => []);
    expect(meshOf(root, "barrel").visible).toBe(false);
    expect(batches.placings).toBe(0);
  });

  it("gives no shadow to a prop too small to matter, or to one whose placings cast none", () => {
    const { root, batches } = setup();
    batches.stale.add("barrel");
    batches.stale.add("lampGlass");
    batches.write(() => [placings(2)]);
    expect(meshOf(root, "barrel").castShadow).toBe(true);
    expect(meshOf(root, "lampGlass").castShadow).toBe(false);
    batches.stale.add("barrel");
    batches.write(() => [placings(2, false)]);
    expect(meshOf(root, "barrel").castShadow).toBe(false);
  });
});
