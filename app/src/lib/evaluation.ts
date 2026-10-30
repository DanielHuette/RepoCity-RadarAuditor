// Rows, order and totals of the Auswertung page: one row per check result with its finding.
import type { Audit, CheckOutcome, CheckResult, Finding } from "../api/types";
import { AREA_IDS, AREA_NAMES, catalogueOrder, checkName, radarLabel, scoredAreas } from "./catalogue";
import { formatMeasured } from "./measured";
import { areaScore } from "./scoring";

export type SortMode = "potential" | "area" | "urgency";

export const SORT_LABELS: Record<SortMode, string> = {
  potential: "Potenzial",
  area: "Bereich",
  urgency: "Dringlichkeit",
};

export interface CheckRow {
  check: CheckResult;
  finding: Finding | null;
  name: string;
  /** Finding severity 1 to 3; 0 when the analysis wrote no finding for the check. */
  urgency: number;
}

export interface AreaGroup {
  area: string;
  name: string;
  rows: CheckRow[];
  potential: number;
}

export interface PotentialTotals {
  points: number;
  upliftLow: number;
  upliftHigh: number;
  openChecks: number;
  /** False while no result carries a potential, for audits scored before potentials existed. */
  known: boolean;
}

const OPEN_OUTCOMES: ReadonlySet<CheckOutcome> = new Set(["failed", "partial"]);
const SCORED_STATUSES: ReadonlySet<Audit["status"]> = new Set(["scored", "awaiting_analysis", "analysed", "reported"]);

const points = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });

/** Supabase returns numeric columns as numbers, older clients as strings; both count. */
function amount(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function potentialOf(check: CheckResult): number {
  return amount(check.potential_points) ?? 0;
}

export function isOpen(check: CheckResult): boolean {
  return OPEN_OUTCOMES.has(check.outcome);
}

export function buildRows(checks: readonly CheckResult[], findings: readonly Finding[]): CheckRow[] {
  const byCheck = new Map(findings.map((finding) => [finding.check_id, finding]));
  return checks.map((check) => {
    const finding = byCheck.get(check.check_id) ?? null;
    return {
      check,
      finding,
      name: checkName(check.check_id),
      urgency: finding?.severity ?? 0,
    };
  });
}

/** Findings whose check has no result in this audit; shown on their own so none gets lost. */
export function orphanFindings(checks: readonly CheckResult[], findings: readonly Finding[]): Finding[] {
  const known = new Set(checks.map((check) => check.check_id));
  return findings.filter((finding) => !known.has(finding.check_id));
}

function byCatalogue(left: CheckRow, right: CheckRow): number {
  return catalogueOrder(left.check.check_id) - catalogueOrder(right.check.check_id)
    || left.check.check_id.localeCompare(right.check.check_id, "de", { numeric: true });
}

function byPotential(left: CheckRow, right: CheckRow): number {
  return potentialOf(right.check) - potentialOf(left.check) || right.urgency - left.urgency || byCatalogue(left, right);
}

function byUrgency(left: CheckRow, right: CheckRow): number {
  return right.urgency - left.urgency || potentialOf(right.check) - potentialOf(left.check) || byCatalogue(left, right);
}

const ROW_ORDER: Record<SortMode, (left: CheckRow, right: CheckRow) => number> = {
  potential: byPotential,
  area: byCatalogue,
  urgency: byUrgency,
};

export function sortRows(rows: readonly CheckRow[], mode: SortMode): CheckRow[] {
  return [...rows].sort(ROW_ORDER[mode]);
}

function areaRank(area: string): number {
  const index = AREA_IDS.indexOf(area);
  return index === -1 ? AREA_IDS.length : index;
}

/**
 * Rows grouped by area. Groups follow the chosen order too: by their open potential, by their
 * most urgent row, or by the catalogue; ties and "Bereich" keep the catalogue order.
 */
export function groupRows(rows: readonly CheckRow[], mode: SortMode, onlyOpen: boolean): AreaGroup[] {
  const groups = new Map<string, CheckRow[]>();
  for (const row of sortRows(rows, mode)) {
    if (onlyOpen && !isOpen(row.check)) continue;
    const list = groups.get(row.check.area) ?? [];
    list.push(row);
    groups.set(row.check.area, list);
  }
  const result = [...groups].map(([area, list]) => ({
    area,
    name: AREA_NAMES[area] ?? `Bereich ${area}`,
    rows: list,
    potential: list.reduce((sum, row) => sum + (isOpen(row.check) ? potentialOf(row.check) : 0), 0),
  }));
  const urgency = (group: AreaGroup) => Math.max(0, ...group.rows.map((row) => row.urgency));
  const groupOrder: Record<SortMode, (left: AreaGroup, right: AreaGroup) => number> = {
    potential: (left, right) => right.potential - left.potential,
    area: () => 0,
    urgency: (left, right) => urgency(right) - urgency(left) || right.potential - left.potential,
  };
  return result.sort((left, right) => groupOrder[mode](left, right) || areaRank(left.area) - areaRank(right.area));
}

export interface Lever {
  checkId: string;
  name: string;
  /** What was measured in one line: the finding's sentence when Claude Code wrote one, else the first
   * plain-language measured value as "Label: Wert"; null when nothing was measured. */
  measured: string | null;
  potential: number;
}

const LEVERS = 3;

/** The open checks with the most open potential, largest first; ties follow the catalogue. */
export function topLevers(checks: readonly CheckResult[], findings: readonly Finding[] = [], count = LEVERS): Lever[] {
  const explained = new Map(findings.map((finding) => [finding.check_id, finding.explanation.trim()]));
  return checks
    .filter((check) => isOpen(check) && potentialOf(check) > 0)
    .sort((left, right) => potentialOf(right) - potentialOf(left) || catalogueOrder(left.check_id) - catalogueOrder(right.check_id))
    .slice(0, count)
    .map((check) => {
      const [line] = formatMeasured(check.measured);
      const sentence = explained.get(check.check_id);
      return {
        checkId: check.check_id,
        name: checkName(check.check_id),
        measured: sentence !== undefined && sentence !== "" ? sentence : line === undefined ? null : `${line.label}: ${line.value}`,
        potential: potentialOf(check),
      };
    });
}

/** "+2,4" for the gold pill of a lever. */
export function formatLeverPoints(value: number): string {
  return `+${points.format(value)}`;
}

/** Id of the row of a check in "Alle Prüfungen", the target of a lever. */
export function checkAnchor(checkId: string): string {
  return `pruefung-${checkId}`;
}

export function potentialTotals(checks: readonly CheckResult[]): PotentialTotals {
  const open = checks.filter(isOpen);
  const sum = (pick: (check: CheckResult) => number | null) =>
    open.reduce((total, check) => total + (pick(check) ?? 0), 0);
  return {
    points: sum((check) => amount(check.potential_points)),
    upliftLow: sum((check) => amount(check.uplift_low)),
    upliftHigh: sum((check) => amount(check.uplift_high)),
    openChecks: open.length,
    known: checks.some((check) => amount(check.potential_points) !== null),
  };
}

export function formatPotential(value: number): string {
  return `+${points.format(value)} ${value === 1 ? "Punkt" : "Punkte"}`;
}

/** "+3 bis 7 Anfragen pro Monat (Schätzung)"; null when nothing is expected. */
export function formatUplift(low: number | string | null, high: number | string | null): string | null {
  const from = amount(low);
  const to = amount(high);
  if (from === null || to === null || to <= 0) return null;
  const range = points.format(from) === points.format(to) ? points.format(to) : `${points.format(from)} bis ${points.format(to)}`;
  return `+${range} ${to === 1 && from === to ? "Anfrage" : "Anfragen"} pro Monat (Schätzung)`;
}

/** The audit of the same customer before `current` that has a score, else null. */
export function previousAudit(history: readonly Audit[], current: Audit): Audit | null {
  const earlier = history
    .filter((audit) => audit.id !== current.id && audit.requested_at < current.requested_at)
    .filter((audit) => SCORED_STATUSES.has(audit.status) && audit.total_score !== null)
    .sort((left, right) => right.requested_at.localeCompare(left.requested_at));
  return earlier[0] ?? null;
}

export interface RadarAxis {
  area: string;
  label: string;
  name: string;
  score: number | null;
  previous: number | null;
}

/** One axis per area that counts towards the total, in catalogue order. */
export function radarAxes(current: readonly CheckResult[], previous: readonly CheckResult[] | null): RadarAxis[] {
  const scoreIn = (checks: readonly CheckResult[], area: string) => areaScore(checks.filter((check) => check.area === area));
  return scoredAreas().map((area) => ({
    area: area.id,
    label: radarLabel(area.id),
    name: area.name,
    score: scoreIn(current, area.id),
    previous: previous === null ? null : scoreIn(previous, area.id),
  }));
}
