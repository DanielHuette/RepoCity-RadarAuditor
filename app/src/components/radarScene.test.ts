// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";
import { FALLBACK_GOLD, advanceMotion, scatterPoints, themeGold } from "./radarScene";

describe("themeGold", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--gold");
  });

  it("takes the gold of the theme tokens", () => {
    document.documentElement.style.setProperty("--gold", " #123456 ");
    expect(themeGold(document.documentElement)).toBe("#123456");
  });

  it("falls back to the brand gold when the token is not set", () => {
    expect(themeGold(document.documentElement)).toBe(FALLBACK_GOLD);
  });
});

describe("scatterPoints", () => {
  it("lays every point flat on the disc and keeps its angle for the beam", () => {
    const points = scatterPoints(() => 0.5);
    expect(points.angle).toHaveLength(2600);
    expect(points.position).toHaveLength(2600 * 3);
    expect(points.angle[0]).toBeCloseTo(Math.PI);
    expect(points.position[0]).toBeCloseTo(-Math.sqrt(0.5) * 3.2, 5);
    expect(points.position[1]).toBe(0);
    expect(points.size[0]).toBeCloseTo(2.1);
  });

  it("moves about a third of the points onto the nearest ring", () => {
    const points = scatterPoints(() => 0.3);
    expect(Math.hypot(points.position[0] ?? 0, points.position[2] ?? 0) / 3.2).toBeCloseTo(0.5 - 0.2 * 0.015, 5);
  });
});

describe("advanceMotion", () => {
  it("turns the beam and lets the tilt follow the pointer", () => {
    const motion = { angle: 0, tiltX: 0, tiltZ: 0, pointerX: 1, pointerY: 1 };
    advanceMotion(motion, 0.1);
    expect(motion.angle).toBeCloseTo(0.055);
    expect(motion.tiltX).toBeCloseTo(-0.07 * 0.3);
    expect(motion.tiltZ).toBeCloseTo(0.09 * 0.3);
  });

  it("wraps the beam angle after a full turn", () => {
    const motion = { angle: Math.PI * 2 - 0.01, tiltX: 0, tiltZ: 0, pointerX: 0, pointerY: 0 };
    advanceMotion(motion, 0.1);
    expect(motion.angle).toBeCloseTo(0.045);
  });
});
