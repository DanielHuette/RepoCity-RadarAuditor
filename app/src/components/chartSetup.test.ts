// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";
import { applyChartDefaults, Chart, token } from "./chartSetup";

afterEach(() => {
  document.documentElement.removeAttribute("style");
});

describe("token", () => {
  it("reads a theme variable", () => {
    document.documentElement.style.setProperty("--chart-line", " rgba(1, 2, 3, 0.5) ");
    expect(token("--chart-line", "black")).toBe("rgba(1, 2, 3, 0.5)");
  });

  it("falls back where the variable is not set", () => {
    expect(token("--chart-line", "rgba(243, 241, 234, 0.08)")).toBe("rgba(243, 241, 234, 0.08)");
  });
});

describe("applyChartDefaults", () => {
  it("draws grid lines in the theme's chart line colour", () => {
    document.documentElement.style.setProperty("--chart-line", "rgba(9, 9, 9, 0.1)");
    applyChartDefaults();
    expect(Chart.defaults.borderColor).toBe("rgba(9, 9, 9, 0.1)");
  });
});
