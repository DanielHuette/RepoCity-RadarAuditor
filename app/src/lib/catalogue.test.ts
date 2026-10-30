import { describe, expect, it } from "vitest";
import { AREA_NAMES, catalogueOrder, checkName, radarLabel, scoredAreas } from "./catalogue";

describe("catalogue lookups", () => {
  it("names checks and areas, with a fallback for unknown ids", () => {
    expect(checkName("b2")).toBe("Titel bei Google nennt, wonach Kunden suchen");
    expect(checkName("x7")).toBe("Prüfung x7");
    expect(AREA_NAMES.k).toBe("Sichtbarkeit in KI-Assistenten");
    expect(radarLabel("k")).toBe("KI-Assistenten");
    expect(radarLabel("n")).toBe("Werkzeuge");
    expect(radarLabel("z")).toBe("Bereich z");
  });

  it("orders checks as in the catalogue and unknown ones last", () => {
    expect(catalogueOrder("a1")).toBeLessThan(catalogueOrder("a10"));
    expect(catalogueOrder("a10")).toBeLessThan(catalogueOrder("b1"));
    expect(catalogueOrder("x1")).toBeGreaterThan(catalogueOrder("m4"));
  });

  it("leaves the weight-0 area out of the scored areas", () => {
    expect(scoredAreas().map((area) => area.id)).toEqual(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "n"]);
  });
});
