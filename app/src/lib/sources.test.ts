import { describe, expect, it } from "vitest";
import {
  DEFAULT_SOURCES,
  SOURCE_IDS,
  costCapRequired,
  monthStart,
  monthUsage,
  parseCostCap,
  parseDailyCap,
  parseSources,
  withSource,
} from "./sources";

describe("parseSources", () => {
  it("falls back to the defaults when nothing is stored", () => {
    const settings = parseSources(null);
    expect(settings).toEqual(DEFAULT_SOURCES);
    expect(settings.places).toEqual({ enabled: true, daily_cap: 500, monthly_cost_cap_eur: 25 });
    expect(settings.prospect_search).toEqual({ enabled: true, daily_cap: 15, monthly_cost_cap_eur: 10 });
    expect(settings.dataforseo.monthly_cost_cap_eur).toBe(50);
    expect(SOURCE_IDS.filter((id) => !settings[id].enabled)).toEqual([]);
  });

  it("keeps stored switches and limits and drops unknown sources", () => {
    const settings = parseSources({ places: { enabled: true, daily_cap: 100 }, openai: { enabled: true } });
    expect(settings.places).toEqual({ enabled: true, daily_cap: 100, monthly_cost_cap_eur: 25 });
    expect(settings).not.toHaveProperty("openai");
    expect(settings.chatgpt).toEqual({ enabled: true, daily_cap: 200 });
  });

  it("replaces broken entries and negative limits", () => {
    const settings = parseSources({ chatgpt: "an", gemini: { enabled: "ja", daily_cap: -3 } });
    expect(settings.chatgpt).toEqual({ enabled: true, daily_cap: 200 });
    expect(settings.gemini).toEqual({ enabled: true, daily_cap: 200 });
  });

  it("keeps the default cost cap when the stored entry has none", () => {
    expect(parseSources({ dataforseo: { enabled: false } }).dataforseo).toEqual({
      enabled: false,
      daily_cap: 1000,
      monthly_cost_cap_eur: 50,
    });
  });

  it("keeps the default cost cap of a paid source when the stored cap is null", () => {
    const settings = parseSources({
      places: { enabled: true, daily_cap: 5, monthly_cost_cap_eur: null },
      prospect_search: { enabled: true, monthly_cost_cap_eur: null },
      dataforseo: { enabled: true, monthly_cost_cap_eur: "x" },
    });
    expect(settings.places).toEqual({ enabled: true, daily_cap: 5, monthly_cost_cap_eur: 25 });
    expect(settings.prospect_search.monthly_cost_cap_eur).toBe(10);
    expect(settings.dataforseo.monthly_cost_cap_eur).toBe(50);
  });

  it("uses the daily caps the worker enforces when none is stored", () => {
    const settings = parseSources({ google_search: { enabled: true } });
    expect(settings.google_search.daily_cap).toBe(300);
    expect(SOURCE_IDS.filter((id) => DEFAULT_SOURCES[id].daily_cap === undefined)).toEqual([]);
  });
});

describe("withSource", () => {
  it("changes one source and removes cleared limits", () => {
    const start = parseSources({ chatgpt: { enabled: true, daily_cap: 20 } });
    const next = withSource(start, "chatgpt", { daily_cap: undefined, enabled: false });
    expect(next.chatgpt).toEqual({ enabled: false });
    expect(next.claude).toBe(start.claude);
    expect(JSON.stringify(next.chatgpt)).toBe('{"enabled":false}');
  });
});

describe("limit inputs", () => {
  it("reads daily caps as whole numbers and requires one", () => {
    expect(parseDailyCap(" 40 ")).toEqual({ value: 40 });
    expect(parseDailyCap("")).toHaveProperty("error");
    expect(parseDailyCap("4,5")).toHaveProperty("error");
  });

  it("reads euro caps with German decimals and requires one for DataForSEO", () => {
    expect(parseCostCap("49,50", true)).toEqual({ value: 49.5 });
    expect(parseCostCap("", false)).toEqual({ value: undefined });
    expect(parseCostCap("", true)).toHaveProperty("error");
    expect(parseCostCap("-1", false)).toHaveProperty("error");
    expect(costCapRequired("dataforseo")).toBe(true);
    expect(costCapRequired("places")).toBe(true);
    expect(costCapRequired("prospect_search")).toBe(true);
    expect(costCapRequired("chatgpt")).toBe(false);
  });
});

describe("monthUsage", () => {
  it("sums the uses of the month per source", () => {
    const rows = [
      { day: "2026-09-30", source: "dataforseo", count: 9 },
      { day: "2026-10-01", source: "dataforseo", count: 3 },
      { day: "2026-10-12", source: "dataforseo", count: 4 },
      { day: "2026-10-12", source: "chatgpt", count: 2 },
    ];
    expect(monthUsage(rows, "2026-10-01")).toEqual({ dataforseo: 7, chatgpt: 2 });
  });

  it("starts the month in Berlin time", () => {
    expect(monthStart(new Date("2026-10-31T23:30:00Z"))).toBe("2026-11-01");
    expect(monthStart(new Date("2026-10-15T08:00:00Z"))).toBe("2026-10-01");
  });
});
