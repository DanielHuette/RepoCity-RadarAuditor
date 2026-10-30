import { describe, expect, it } from "vitest";
import { buildGrid, parseGridPoint } from "./grid";
import { ranking } from "./testRows";

describe("parseGridPoint", () => {
  it("reads row and column", () => {
    expect(parseGridPoint("r2c3")).toEqual({ row: 2, column: 3 });
    expect(parseGridPoint(" R10C1 ")).toEqual({ row: 10, column: 1 });
  });

  it("rejects other formats", () => {
    expect(parseGridPoint("51.96,7.63")).toBeNull();
    expect(parseGridPoint("r2")).toBeNull();
    expect(parseGridPoint("")).toBeNull();
  });
});

describe("buildGrid", () => {
  it("places one-based points into an n×n grid", () => {
    const grid = buildGrid([
      ranking({ grid_point: "r1c1", local_pack_position: 1 }),
      ranking({ grid_point: "r3c3", local_pack_position: 7 }),
      ranking({ grid_point: "r2c2", local_pack_position: null }),
    ]);
    expect(grid?.size).toBe(3);
    expect(grid?.cells).toEqual([
      [1, undefined, undefined],
      [undefined, null, undefined],
      [undefined, undefined, 7],
    ]);
  });

  it("anchors zero-based points at zero", () => {
    const grid = buildGrid([ranking({ grid_point: "r0c0", local_pack_position: 2 }), ranking({ grid_point: "r1c1", local_pack_position: 4 })]);
    expect(grid?.size).toBe(2);
    expect(grid?.cells).toEqual([
      [2, undefined],
      [undefined, 4],
    ]);
  });

  it("keeps the best position when a point was measured twice", () => {
    const grid = buildGrid([
      ranking({ grid_point: "r1c1", local_pack_position: null }),
      ranking({ grid_point: "r1c1", local_pack_position: 5 }),
      ranking({ grid_point: "r1c1", local_pack_position: 3 }),
    ]);
    expect(grid?.cells).toEqual([[3]]);
  });

  it("returns null without grid points", () => {
    expect(buildGrid([ranking({ grid_point: null }), ranking({ grid_point: "51.9,7.6" })])).toBeNull();
  });
});
