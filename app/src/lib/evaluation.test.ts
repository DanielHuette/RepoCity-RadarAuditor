import { describe, expect, it } from "vitest";
import type { CheckOutcome, CheckResult } from "../api/types";
import {
  buildRows,
  checkAnchor,
  formatLeverPoints,
  topLevers,
  formatPotential,
  formatUplift,
  groupRows,
  orphanFindings,
  potentialTotals,
  previousAudit,
  radarAxes,
  sortRows,
} from "./evaluation";
import { audit, check, finding } from "./testRows";

function scored(checkId: string, outcome: CheckOutcome, potential: number | null, uplift: [number, number] | null = null): CheckResult {
  return {
    ...check(checkId.replace(/\d+$/, ""), outcome, checkId),
    potential_points: potential,
    uplift_low: uplift?.[0] ?? null,
    uplift_high: uplift?.[1] ?? null,
  };
}

const ids = (rows: { check: CheckResult }[]) => rows.map((row) => row.check.check_id);

describe("buildRows", () => {
  it("joins findings and takes names from the catalogue", () => {
    const [row, unknown] = buildRows(
      [scored("a1", "failed", 2), scored("z9", "passed", 0)],
      [finding({ check_id: "a1", severity: 3 })],
    );
    expect(row).toMatchObject({ name: "Verschlüsselte Seite unter einer einzigen Adresse", urgency: 3 });
    expect(unknown).toMatchObject({ name: "Prüfung z9", urgency: 0, finding: null });
  });
});

describe("sortRows", () => {
  const rows = buildRows(
    [scored("a2", "partial", 1), scored("b1", "failed", 4), scored("a1", "failed", 4), scored("c1", "passed", null)],
    [finding({ check_id: "a2", severity: 3 }), finding({ check_id: "b1", severity: 1 })],
  );

  it("puts the largest potential first, then the more urgent, then catalogue order", () => {
    expect(ids(sortRows(rows, "potential"))).toEqual(["b1", "a1", "a2", "c1"]);
  });

  it("orders by urgency, then potential", () => {
    expect(ids(sortRows(rows, "urgency"))).toEqual(["a2", "b1", "a1", "c1"]);
  });

  it("keeps the catalogue order for the area view", () => {
    expect(ids(sortRows(rows, "area"))).toEqual(["a1", "a2", "b1", "c1"]);
  });

  it("orders checks with two-digit numbers naturally", () => {
    expect(ids(sortRows(buildRows([scored("a10", "passed", 0), scored("a9", "passed", 0)], []), "area"))).toEqual(["a9", "a10"]);
  });
});

describe("groupRows", () => {
  const rows = buildRows(
    [scored("a1", "failed", 1), scored("a2", "passed", 0), scored("c1", "failed", 5), scored("b1", "passed", 0)],
    [finding({ check_id: "a1", severity: 3 })],
  );

  it("orders areas by their open potential when sorting by potential", () => {
    const groups = groupRows(rows, "potential", false);
    expect(groups.map((group) => [group.area, group.potential])).toEqual([["c", 5], ["a", 1], ["b", 0]]);
    expect(groups[0]?.name).toBe("Inhalte und Leistungsseiten");
  });

  it("orders areas by their most urgent check", () => {
    expect(groupRows(rows, "urgency", false).map((group) => group.area)).toEqual(["a", "c", "b"]);
  });

  it("keeps catalogue order for areas", () => {
    expect(groupRows(rows, "area", false).map((group) => group.area)).toEqual(["a", "b", "c"]);
  });

  it("keeps only failed and partial checks with the open filter", () => {
    const groups = groupRows(rows, "area", true);
    expect(groups.map((group) => ids(group.rows))).toEqual([["a1"], ["c1"]]);
  });
});

describe("potentialTotals", () => {
  it("adds potential and uplift of the open checks only", () => {
    const totals = potentialTotals([
      scored("a1", "failed", 2.5, [1, 3]),
      scored("a2", "partial", 1, [0.5, 1.5]),
      scored("a3", "passed", 0, [0, 0]),
      scored("a4", "not_measurable", 3, [2, 4]),
    ]);
    expect(totals).toEqual({ points: 3.5, upliftLow: 1.5, upliftHigh: 4.5, openChecks: 2, known: true });
  });

  it("reports unknown potential for audits scored before potentials existed", () => {
    expect(potentialTotals([scored("a1", "failed", null)])).toMatchObject({ points: 0, known: false, openChecks: 1 });
  });
});

describe("formatPotential and formatUplift", () => {
  it("writes points with a sign and German decimals", () => {
    expect(formatPotential(2.5)).toBe("+2,5 Punkte");
    expect(formatPotential(1)).toBe("+1 Punkt");
  });

  it("writes the uplift as a range labelled as an estimate", () => {
    expect(formatUplift(3, 7)).toBe("+3 bis 7 Anfragen pro Monat (Schätzung)");
    expect(formatUplift(0.4, 1.25)).toBe("+0,4 bis 1,3 Anfragen pro Monat (Schätzung)");
    expect(formatUplift(2, 2)).toBe("+2 Anfragen pro Monat (Schätzung)");
    expect(formatUplift(1, 1)).toBe("+1 Anfrage pro Monat (Schätzung)");
  });

  it("returns nothing when no uplift is expected or known", () => {
    expect(formatUplift(0, 0)).toBeNull();
    expect(formatUplift(null, 4)).toBeNull();
  });
});

describe("previousAudit", () => {
  const current = audit({ id: "now", requested_at: "2026-10-01T08:00:00Z", total_score: 63 });

  it("picks the latest earlier audit with a score", () => {
    const history = [
      current,
      audit({ id: "failed", requested_at: "2026-09-20T08:00:00Z", status: "failed", total_score: null }),
      audit({ id: "august", requested_at: "2026-08-04T08:00:00Z", total_score: 41 }),
      audit({ id: "june", requested_at: "2026-06-01T08:00:00Z", total_score: 30 }),
      audit({ id: "later", requested_at: "2026-10-15T08:00:00Z", total_score: 70 }),
    ];
    expect(previousAudit(history, current)?.id).toBe("august");
  });

  it("returns null for the first audit of a customer", () => {
    expect(previousAudit([current], current)).toBeNull();
  });
});

describe("radarAxes", () => {
  it("gives one axis per weighted area with current and previous score", () => {
    const axes = radarAxes([scored("a1", "passed", 0), scored("a2", "failed", 2)], [scored("a1", "failed", 2)]);
    expect(axes).toHaveLength(13);
    expect(axes[0]).toEqual({ area: "a", label: "Technik", name: "Technik der Website", score: 50, previous: 0 });
    expect(axes[1]).toMatchObject({ area: "b", score: null, previous: null });
    expect(axes.some((axis) => axis.area === "m")).toBe(false);
  });

  it("has no previous scores without a previous audit", () => {
    expect(radarAxes([scored("a1", "passed", 0)], null).every((axis) => axis.previous === null)).toBe(true);
  });
});

describe("orphanFindings", () => {
  it("returns findings whose check has no result", () => {
    const orphan = finding({ check_id: "x1" });
    expect(orphanFindings([scored("a1", "failed", 1)], [finding({ check_id: "a1" }), orphan])).toEqual([orphan]);
  });
});

describe("topLevers", () => {
  it("picks the three open checks with the most potential, largest first", () => {
    const checks = [
      scored("a1", "failed", 1.5),
      scored("g1", "partial", 4),
      { ...scored("g4", "failed", 2.25), measured: { reply_rate: 0.4, replied: 4 } },
      scored("b2", "failed", 0.5),
      scored("c1", "passed", 9),
    ];
    const levers = topLevers(checks);
    expect(levers.map((lever) => [lever.checkId, lever.potential])).toEqual([["g1", 4], ["g4", 2.25], ["a1", 1.5]]);
    expect(levers[1]).toEqual({ checkId: "g4", name: expect.any(String) as string, measured: "Beantwortet: 4", potential: 2.25 });
    expect(levers[0]?.measured).toBeNull();
  });

  it("leaves out checks without open potential and keeps catalogue order on ties", () => {
    const levers = topLevers([scored("b1", "failed", 2), scored("a2", "failed", 2), scored("a3", "failed", 0), scored("a4", "partial", null)]);
    expect(levers.map((lever) => lever.checkId)).toEqual(["a2", "b1"]);
    expect(topLevers([])).toEqual([]);
  });

  it("uses the finding's sentence as the measured line when there is one", () => {
    const check = scored("g4", "failed", 2);
    const finding = { id: "f1", audit_id: "a1", check_id: "g4", severity: 2 as const, title: "t", explanation: "Von zehn neuen Bewertungen haben Sie vier beantwortet.", customer_impact: "c", fix: "x", fix_payload: null };
    expect(topLevers([check], [finding])[0]?.measured).toBe("Von zehn neuen Bewertungen haben Sie vier beantwortet.");
  });

  it("formats the pill and names the row anchor", () => {
    expect(formatLeverPoints(2.25)).toBe("+2,3");
    expect(formatLeverPoints(4)).toBe("+4");
    expect(checkAnchor("g4")).toBe("pruefung-g4");
  });
});
