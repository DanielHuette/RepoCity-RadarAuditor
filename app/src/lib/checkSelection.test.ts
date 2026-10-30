import { afterEach, describe, expect, it, vi } from "vitest";
import { CATALOGUE_AREAS, type CatalogueArea } from "./catalogue";
import {
  AREA_ORDER,
  SELECTION_STORAGE_KEY,
  areaState,
  areaTags,
  checkTags,
  defaultDisabled,
  deviceStorage,
  enabledCount,
  loadSelection,
  orderedAreas,
  saveSelection,
  setAll,
  setArea,
  setCheck,
} from "./checkSelection";
import { catalogueCheck as check } from "./testRows";

const g: CatalogueArea = { id: "g", name: "Bewertungen", weight: 10, checks: [check("g1"), check("g2")] };
const m: CatalogueArea = {
  id: "m",
  name: "Wirkung mit Kundenzugang",
  weight: 0,
  checks: [check("m1", { requiresAccess: true }), check("m2", { requiresAccess: true })],
};

function memoryStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value);
    },
    items,
  };
}

const throwing = {
  getItem: (): string | null => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
};

describe("the catalogue on the Prüfen page", () => {
  it("lists the 14 areas in the agreed order with 108 checks", () => {
    expect(orderedAreas().map((area) => area.id)).toEqual(["f", "g", "a", "b", "c", "d", "e", "h", "i", "j", "k", "l", "n", "m"]);
    expect(orderedAreas().map((area) => area.id)).toEqual(AREA_ORDER);
    expect(enabledCount([], CATALOGUE_AREAS).total).toBe(108);
  });

  it("gives every check a name and a reason", () => {
    const checks = CATALOGUE_AREAS.flatMap((area) => area.checks);
    expect(checks.every((item) => item.name.trim() !== "" && item.why.trim() !== "")).toBe(true);
    expect(new Set(checks.map((item) => item.id)).size).toBe(checks.length);
  });

  it("puts areas missing from the order after the known ones", () => {
    const z: CatalogueArea = { id: "z", name: "Neu", weight: 1, checks: [check("z1")] };
    expect(orderedAreas([z, m, g]).map((area) => area.id)).toEqual(["g", "m", "z"]);
  });
});

describe("tags", () => {
  const byId = (id: string) => CATALOGUE_AREAS.find((area) => area.id === id);

  it("marks the new area, paid checks and the customer-access area", () => {
    expect(areaTags(byId("n") ?? g)).toEqual([{ label: "neu", isNew: true }]);
    expect(areaTags(byId("i") ?? g)).toEqual([{ label: "4 kosten Geld", isNew: false }]);
    expect(areaTags(byId("j") ?? g)).toEqual([{ label: "1 kostet Geld", isNew: false }]);
    expect(areaTags(byId("m") ?? g)).toEqual([{ label: "braucht Kundenzugang", isNew: false }]);
    expect(areaTags(byId("a") ?? m)).toEqual([]);
  });

  it("marks the new checks and the paid ones", () => {
    const checks = new Map(CATALOGUE_AREAS.flatMap((area) => area.checks.map((item) => [item.id, item] as const)));
    for (const id of ["f15", "n1", "n2", "n3"]) expect(checkTags(checks.get(id) ?? check(id))).toContainEqual({ label: "neu", isNew: true });
    expect(checkTags(checks.get("c2") ?? check("c2"))).toEqual([{ label: "kostet Geld", isNew: false }]);
    expect(checkTags(checks.get("a1") ?? check("a1"))).toEqual([]);
  });
});

describe("switching checks", () => {
  it("switches one check, one area and everything", () => {
    expect(setCheck([], "g1", false)).toEqual(["g1"]);
    expect(setCheck(["g1"], "g1", true)).toEqual([]);
    expect(setArea(["m1"], g, false)).toEqual(["g1", "g2", "m1"]);
    expect(setArea(["g1", "g2", "m1"], g, true)).toEqual(["m1"]);
    expect(setAll([g, m], false)).toEqual(["g1", "g2", "m1", "m2"]);
    expect(setAll([g, m], true)).toEqual([]);
  });

  it("tells whether an area is fully, partly or not on", () => {
    expect(areaState([], g)).toBe("all");
    expect(areaState(["g1"], g)).toBe("some");
    expect(areaState(["g1", "g2"], g)).toBe("none");
  });

  it("counts enabled checks; ids no longer in the catalogue do not count", () => {
    expect(enabledCount(["g1", "x9"], [g, m])).toEqual({ enabled: 3, total: 4 });
  });
});

describe("default and remembered selection", () => {
  it("switches on everything except the customer-access area by default", () => {
    expect(defaultDisabled([g, m])).toEqual(["m1", "m2"]);
    expect(defaultDisabled()).toEqual(["m1", "m2", "m3", "m4"]);
    expect(enabledCount(defaultDisabled(), CATALOGUE_AREAS)).toEqual({ enabled: 104, total: 108 });
  });

  it("uses the default without storage, with nothing stored or with unreadable data", () => {
    expect(loadSelection(null, [g, m])).toEqual(["m1", "m2"]);
    expect(loadSelection(memoryStorage(), [g, m])).toEqual(["m1", "m2"]);
    expect(loadSelection(memoryStorage({ [SELECTION_STORAGE_KEY]: "{kaputt" }), [g, m])).toEqual(["m1", "m2"]);
    expect(loadSelection(memoryStorage({ [SELECTION_STORAGE_KEY]: '{"g1":true}' }), [g, m])).toEqual(["m1", "m2"]);
    expect(loadSelection(throwing, [g, m])).toEqual(["m1", "m2"]);
  });

  it("brings back the last selection, without ids that left the catalogue", () => {
    const storage = memoryStorage();
    saveSelection(storage, ["g2", "x9"]);
    expect(storage.items.get(SELECTION_STORAGE_KEY)).toBe('["g2","x9"]');
    expect(loadSelection(storage, [g, m])).toEqual(["g2"]);
    saveSelection(storage, []);
    expect(loadSelection(storage, [g, m])).toEqual([]);
  });

  it("keeps working when storage refuses to save", () => {
    expect(() => {
      saveSelection(throwing, ["g1"]);
    }).not.toThrow();
    expect(() => {
      saveSelection(null, ["g1"]);
    }).not.toThrow();
  });
});

describe("deviceStorage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is the localStorage of the browser", () => {
    const storage = { getItem: () => null, setItem: () => undefined };
    vi.stubGlobal("window", { localStorage: storage });
    expect(deviceStorage()).toBe(storage);
  });

  it("is null where reading localStorage throws", () => {
    vi.stubGlobal("window", {
      get localStorage(): Storage {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    expect(deviceStorage()).toBeNull();
  });
});
