import type { Audit, CheckOutcome, CheckResult, Finding } from "../api/types";
import { formatDate } from "./format";
import { AREA_IDS, AREA_NAMES } from "./catalogue";

export type Tone = "ok" | "warn" | "bad" | "none";

export interface AreaSummary {
  area: string;
  name: string;
  score: number | null;
  tone: Tone;
  counts: Record<CheckOutcome, number>;
}

type ScoredOutcome = Exclude<CheckOutcome, "not_measurable" | "skipped">;

const POINTS: Record<ScoredOutcome, number> = {
  passed: 1,
  partial: 0.5,
  failed: 0,
};

const GOOD_FROM = 75;
const FAIR_FROM = 50;

export function scoreTone(score: number | null): Tone {
  if (score === null) return "none";
  if (score >= GOOD_FROM) return "ok";
  if (score >= FAIR_FROM) return "warn";
  return "bad";
}

function emptyCounts(): Record<CheckOutcome, number> {
  return { passed: 0, partial: 0, failed: 0, not_measurable: 0, skipped: 0 };
}

/** Same rule as scoring/evaluator.py: unmeasurable and deselected checks are left out of the area share. */
export function areaScore(checks: readonly CheckResult[]): number | null {
  const points = checks.flatMap((check) =>
    check.outcome === "not_measurable" || check.outcome === "skipped" ? [] : [POINTS[check.outcome]],
  );
  if (points.length === 0) return null;
  const share = points.reduce((sum, value) => sum + value, 0) / points.length;
  return Math.round(share * 10000) / 100;
}

export function groupByArea(checks: readonly CheckResult[]): AreaSummary[] {
  const byArea = new Map<string, CheckResult[]>();
  for (const check of checks) {
    const list = byArea.get(check.area) ?? [];
    list.push(check);
    byArea.set(check.area, list);
  }
  const known = AREA_IDS.filter((area) => byArea.has(area));
  const unknown = [...byArea.keys()].filter((area) => !AREA_IDS.includes(area)).sort();
  return [...known, ...unknown].map((area) => {
    const list = byArea.get(area) ?? [];
    const counts = emptyCounts();
    for (const check of list) counts[check.outcome] += 1;
    const score = areaScore(list);
    return { area, name: AREA_NAMES[area] ?? `Bereich ${area}`, score, tone: scoreTone(score), counts };
  });
}

export function sortFindings(findings: readonly Finding[]): Finding[] {
  return [...findings].sort(
    (left, right) =>
      right.severity - left.severity ||
      left.check_id.localeCompare(right.check_id, "de", { numeric: true }),
  );
}

export interface ScorePoint {
  label: string;
  score: number;
}

export function scoreHistory(audits: readonly Audit[]): ScorePoint[] {
  return audits
    .filter((audit): audit is Audit & { total_score: number } => audit.total_score !== null)
    .map((audit) => ({ at: audit.finished_at ?? audit.requested_at, score: audit.total_score }))
    .sort((left, right) => left.at.localeCompare(right.at))
    .map((point) => ({ label: formatDate(point.at), score: point.score }));
}
