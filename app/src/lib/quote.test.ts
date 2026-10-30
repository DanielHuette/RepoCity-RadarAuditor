import { describe, expect, it } from "vitest";
import type { CrmProduct } from "../api/types";
import {
  buildQuoteItems,
  computeTotals,
  openChecksByArea,
  parseQuantity,
  proposePositions,
  quoteTexts,
  type CheckOutcomeRow,
  type Position,
} from "./quote";

function product(overrides: Partial<CrmProduct>): CrmProduct {
  return {
    id: "p",
    name: "Produkt",
    description: "",
    unit: "Pauschale",
    price_net: 100,
    vat_rate: 19,
    billing: "once",
    active: true,
    radar_areas: [],
    ...overrides,
  };
}

function position(overrides: Partial<Position>): Position {
  return {
    productId: "p",
    title: "Produkt",
    description: "",
    unit: "Pauschale",
    unitPriceNet: 100,
    vatRate: 19,
    billing: "once",
    areas: ["a"],
    openChecks: 1,
    quantity: 1,
    enabled: true,
    ...overrides,
  };
}

const CHECKS: CheckOutcomeRow[] = [
  { check_id: "a1", area: "a", outcome: "failed" },
  { check_id: "a2", area: "a", outcome: "partial" },
  { check_id: "a3", area: "a", outcome: "passed" },
  { check_id: "f1", area: "f", outcome: "failed" },
  { check_id: "g1", area: "g", outcome: "passed" },
  { check_id: "i1", area: "i", outcome: "not_measurable" },
  { check_id: "k1", area: "k", outcome: "skipped" },
];

describe("openChecksByArea", () => {
  it("counts only failed and partial checks", () => {
    expect([...openChecksByArea(CHECKS)]).toEqual([
      ["a", 2],
      ["f", 1],
    ]);
  });
});

describe("proposePositions", () => {
  it("proposes each product once whose areas have open checks, one-off before monthly", () => {
    const positions = proposePositions(CHECKS, [
      product({ id: "care", name: "Laufende Betreuung", billing: "monthly", price_net: 290, radar_areas: ["f", "g"] }),
      product({ id: "setup", name: "Erstoptimierung", radar_areas: ["f", "a", "zz"] }),
      product({ id: "setup", name: "Erstoptimierung", radar_areas: ["a"] }),
      product({ id: "links", name: "Linkaufbau", radar_areas: ["i"] }),
      product({ id: "old", name: "Altprodukt", active: false, radar_areas: ["a"] }),
    ]);
    expect(positions.map((item) => item.productId)).toEqual(["setup", "care"]);
    expect(positions[0]).toMatchObject({ areas: ["a", "f"], openChecks: 3, quantity: 1, enabled: true, billing: "once" });
    expect(positions[1]).toMatchObject({ areas: ["f"], openChecks: 1, billing: "monthly", unitPriceNet: 290 });
  });

  it("proposes nothing when the CRM has no products", () => {
    expect(proposePositions(CHECKS, [])).toEqual([]);
  });

  it("proposes nothing when every check passed", () => {
    const passed = CHECKS.map((check) => ({ ...check, outcome: "passed" as const }));
    expect(proposePositions(passed, [product({ radar_areas: ["a"] })])).toEqual([]);
  });
});

describe("parseQuantity", () => {
  it("accepts whole numbers from 1 to 999", () => {
    expect(parseQuantity(" 3 ")).toBe(3);
    expect(parseQuantity("999")).toBe(999);
  });

  it("rejects zero, fractions, text and empty input", () => {
    for (const input of ["0", "1,5", "1000", "zwei", "", "-1"]) expect(parseQuantity(input)).toBeNull();
  });
});

describe("computeTotals", () => {
  it("sums enabled positions with quantity and VAT per rate", () => {
    const totals = computeTotals(
      [
        position({ unitPriceNet: 490, quantity: 2 }),
        position({ unitPriceNet: 290, billing: "monthly" }),
        position({ unitPriceNet: 50, vatRate: 7 }),
        position({ unitPriceNet: 1000, enabled: false }),
      ],
      false,
    );
    expect(totals).toEqual({ net: 1320, vat: 244.8, gross: 1564.8, onceNet: 1030, monthlyNet: 290 });
  });

  it("rounds VAT on the sum per rate like the CRM", () => {
    // 19 % of the sum 99.99 is 19.00; rounding each line (6.33 three times) would give 18.99.
    const totals = computeTotals([1, 2, 3].map((n) => position({ productId: String(n), unitPriceNet: 33.33 })), false);
    expect(totals.vat).toBe(19);
    expect(totals.gross).toBe(118.99);
  });

  it("charges no VAT for a small business", () => {
    expect(computeTotals([position({ unitPriceNet: 490 })], true)).toMatchObject({ net: 490, vat: 0, gross: 490 });
  });

  it("is zero without enabled positions", () => {
    expect(computeTotals([position({ enabled: false })], false)).toEqual({ net: 0, vat: 0, gross: 0, onceNet: 0, monthlyNet: 0 });
  });
});

describe("buildQuoteItems", () => {
  it("numbers the enabled positions and names the billing", () => {
    const items = buildQuoteItems("q1", [
      position({ productId: "setup", title: "Erstoptimierung", description: "Einmalige Umsetzung", areas: ["a", "f"] }),
      position({ productId: "skip", enabled: false }),
      position({ productId: "care", title: "Laufende Betreuung", billing: "monthly", quantity: 2, unitPriceNet: 290 }),
    ]);
    expect(items).toEqual([
      {
        quote_id: "q1",
        position: 1,
        product_id: "setup",
        title: "Erstoptimierung, einmalig",
        description: "Einmalige Umsetzung\nBehebt die offenen Punkte in: Technik der Website, Google-Unternehmensprofil.",
        quantity: 1,
        unit: "Pauschale",
        unit_price_net: 100,
        vat_rate: 19,
      },
      {
        quote_id: "q1",
        position: 2,
        product_id: "care",
        title: "Laufende Betreuung, monatlich",
        description: "Behebt die offenen Punkte in: Technik der Website.",
        quantity: 2,
        unit: "Pauschale",
        unit_price_net: 290,
        vat_rate: 19,
      },
    ]);
  });

  it("keeps titles within the CRM limit of 300 characters", () => {
    const [item] = buildQuoteItems("q1", [position({ title: "x".repeat(400), billing: "monthly" })]);
    expect(item?.title).toHaveLength(300);
    expect(item?.title.endsWith(", monatlich")).toBe(true);
  });
});

describe("quoteTexts", () => {
  it("refers to score, audit date and covered areas", () => {
    const { intro, outro } = quoteTexts({ score: 62.6, auditDate: "2026-10-01T08:30:00+02:00", areas: ["f", "a", "g", "a"] });
    expect(intro).toBe(
      "vielen Dank für das Gespräch. Die Prüfung Ihres Online-Auftritts vom 01.10.2026 hat 63 von 100 Punkten ergeben. " +
        "Die folgenden Leistungen beheben die offenen Punkte in den Bereichen Technik der Website, " +
        "Google-Unternehmensprofil und Bewertungen:",
    );
    expect(outro).toContain("Vorher-Nachher-Vergleich");
  });

  it("works without score, date and areas", () => {
    const { intro } = quoteTexts({ score: null, auditDate: null, areas: [] });
    expect(intro).toBe(
      "vielen Dank für das Gespräch. Die Prüfung Ihres Online-Auftritts zeigt, wo Kunden Sie heute noch nicht finden. " +
        "Gern biete ich Ihnen folgende Leistungen an:",
    );
  });
});
