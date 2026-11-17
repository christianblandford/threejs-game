import { describe, expect, it } from "vitest";
import { highestWater, type Water } from "./waterSurface";

/** A level square of water from (x0, z0) to (x1, z1). */
function pond(x0: number, z0: number, x1: number, z1: number, level: number): Water {
  return { minX: x0, maxX: x1, minZ: z0, maxZ: z1, level, triangles: new Float32Array([x0, z0, x1, z0, x1, z1, x0, z0, x1, z1, x0, z1]) };
}

describe("the water over a point", () => {
  it("is the level of the water the point is over", () => {
    expect(highestWater([pond(0, 0, 10, 10, 1.5)], 5, 5, null)).toBe(1.5);
  });

  it("is nothing beside the water", () => {
    expect(highestWater([pond(0, 0, 10, 10, 1.5)], 11, 5, null)).toBeNull();
  });

  it("is the higher of two waters one over the other", () => {
    expect(highestWater([pond(0, 0, 10, 10, 1.5), pond(2, 2, 8, 8, 3)], 5, 5, null)).toBe(3);
    expect(highestWater([pond(0, 0, 10, 10, 1.5), pond(2, 2, 8, 8, 3)], 1, 1, null)).toBe(1.5);
  });

  it("keeps what was found elsewhere unless this is higher", () => {
    expect(highestWater([pond(0, 0, 10, 10, 1.5)], 5, 5, 4)).toBe(4);
    expect(highestWater([pond(0, 0, 10, 10, 1.5)], 5, 5, 1)).toBe(1.5);
  });
});
