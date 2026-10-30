import { describe, expect, it } from "vitest";
import { INDUSTRY_GROUPS, INDUSTRY_OPTIONS, industryLabel, resolveIndustry } from "./industries";

describe("industry groups", () => {
  it("lists the twelve groups of config/branchen.yaml in order", () => {
    expect(INDUSTRY_GROUPS.map((group) => group.id)).toEqual([
      "bauhandwerk",
      "haustechnik",
      "holz_innenausbau",
      "kfz",
      "friseur_kosmetik",
      "gastronomie",
      "baeckerei_lebensmittel",
      "arzt_zahnarzt",
      "therapie_pflege",
      "recht_steuern_beratung",
      "fitness_sport",
      "handel_dienstleistung",
    ]);
  });

  it("offers each group with its examples and stores the id", () => {
    expect(INDUSTRY_OPTIONS[1]).toEqual({ value: "haustechnik", label: "Haustechnik (Elektro, Sanitär, Heizung, Solar)" });
    expect(INDUSTRY_OPTIONS.find((option) => option.value === "arzt_zahnarzt")?.label).toBe("Arzt und Zahnarzt");
  });
});

describe("resolveIndustry", () => {
  it.each([
    ["haustechnik", "haustechnik"],
    [" Haustechnik ", "haustechnik"],
    ["Handwerk", "bauhandwerk"],
    ["medizin", "arzt_zahnarzt"],
    ["Recht und Steuern", "recht_steuern_beratung"],
    ["recht_steuern", "recht_steuern_beratung"],
    ["Immobilien", "handel_dienstleistung"],
    ["Gastronomie", "gastronomie"],
    ["Kfz", "kfz"],
  ])("finds the group of %s", (stored, id) => {
    expect(resolveIndustry(stored)?.id).toBe(id);
  });

  it("finds no group for values outside every group", () => {
    expect(resolveIndustry("Sonstiges")).toBeUndefined();
  });
});

describe("industryLabel", () => {
  it("names ids and older values by their group", () => {
    expect(industryLabel("fitness_sport")).toBe("Fitness, Yoga, Sport");
    expect(industryLabel("Handwerk")).toBe("Bauhandwerk");
  });

  it("shows values outside every group as stored", () => {
    expect(industryLabel(" Sonstiges ")).toBe("Sonstiges");
  });
});
