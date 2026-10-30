import { describe, expect, it } from "vitest";
import { areaScore, groupByArea, scoreHistory, scoreTone, sortFindings } from "./scoring";
import { audit, check, finding } from "./testRows";

describe("areaScore", () => {
  it("counts passed as 1, partial as 0.5 and failed as 0", () => {
    expect(areaScore([check("a", "passed"), check("a", "partial"), check("a", "failed"), check("a", "failed")])).toBe(37.5);
  });

  it("leaves unmeasurable checks out of the share", () => {
    expect(areaScore([check("a", "passed"), check("a", "not_measurable")])).toBe(100);
  });

  it("leaves deselected checks out of the share", () => {
    expect(areaScore([check("a", "passed"), check("a", "skipped", "a2")])).toBe(100);
    expect(areaScore([check("a", "skipped")])).toBeNull();
  });

  it("returns null when nothing in the area was measurable", () => {
    expect(areaScore([check("a", "not_measurable")])).toBeNull();
    expect(areaScore([])).toBeNull();
  });

  it("rounds to two decimals like the evaluator", () => {
    expect(areaScore([check("a", "passed"), check("a", "failed"), check("a", "failed")])).toBe(33.33);
  });
});

describe("scoreTone", () => {
  it("maps scores to traffic-light tones", () => {
    expect(scoreTone(80)).toBe("ok");
    expect(scoreTone(75)).toBe("ok");
    expect(scoreTone(60)).toBe("warn");
    expect(scoreTone(49.99)).toBe("bad");
    expect(scoreTone(null)).toBe("none");
  });
});

describe("groupByArea", () => {
  it("groups in catalogue order with German names and counts", () => {
    const areas = groupByArea([check("f", "failed"), check("a", "passed"), check("a", "partial", "a2"), check("f", "not_measurable", "f2")]);
    expect(areas.map((area) => area.area)).toEqual(["a", "f"]);
    expect(areas[0]).toMatchObject({ name: "Technik der Website", score: 75, tone: "ok" });
    expect(areas[1]).toMatchObject({ name: "Google-Unternehmensprofil", score: 0, tone: "bad" });
    expect(areas[1]?.counts).toEqual({ passed: 0, partial: 0, failed: 1, not_measurable: 1, skipped: 0 });
  });

  it("keeps unknown areas at the end with a fallback name", () => {
    const areas = groupByArea([check("z", "passed"), check("b", "passed")]);
    expect(areas.map((area) => area.name)).toEqual(["Aufbau der Seiten", "Bereich z"]);
  });

  it("returns nothing for no checks", () => {
    expect(groupByArea([])).toEqual([]);
  });
});

describe("sortFindings", () => {
  it("puts the most severe first and orders checks naturally within a severity", () => {
    const sorted = sortFindings([
      finding({ severity: 1, check_id: "a1" }),
      finding({ severity: 3, check_id: "b10" }),
      finding({ severity: 3, check_id: "b2" }),
      finding({ severity: 2, check_id: "c1" }),
    ]);
    expect(sorted.map((item) => item.check_id)).toEqual(["b2", "b10", "c1", "a1"]);
  });
});

describe("scoreHistory", () => {
  it("lists scored audits oldest first and skips unscored ones", () => {
    const history = scoreHistory([
      audit({ total_score: 72, finished_at: "2026-10-02T10:00:00Z" }),
      audit({ total_score: null }),
      audit({ total_score: 55, finished_at: null, requested_at: "2026-09-01T10:00:00Z" }),
    ]);
    expect(history).toEqual([
      { label: "01.09.2026", score: 55 },
      { label: "02.10.2026", score: 72 },
    ]);
  });
});
