import type { Keyword, Ranking } from "../api/types";

export type RankingMode = "organic" | "local";

export interface RankingSeries {
  subject: string;
  positions: (number | null)[];
}

export interface RankingMatrix {
  keywords: string[];
  series: RankingSeries[];
}

export const POSITION_CUTOFF: Record<RankingMode, number> = { organic: 20, local: 10 };

function position(ranking: Ranking, mode: RankingMode): number | null {
  return mode === "organic" ? ranking.organic_position : ranking.local_pack_position;
}

function sameSubject(left: string, right: string): boolean {
  return left.trim().toLocaleLowerCase("de") === right.trim().toLocaleLowerCase("de");
}

/**
 * Best position per subject and keyword, ignoring grid measurements. The customer comes
 * first, competitors follow in the order they were measured.
 */
export function rankingMatrix(
  rankings: readonly Ranking[],
  keywords: readonly Keyword[],
  customerName: string,
  mode: RankingMode,
): RankingMatrix {
  const plain = rankings.filter((ranking) => ranking.grid_point === null);
  const measured = keywords.filter((keyword) => plain.some((ranking) => ranking.keyword_id === keyword.id));
  const subjects: string[] = [];
  for (const ranking of plain) {
    if (!subjects.some((subject) => sameSubject(subject, ranking.subject))) subjects.push(ranking.subject);
  }
  subjects.sort((left, right) => Number(sameSubject(right, customerName)) - Number(sameSubject(left, customerName)));
  const series = subjects.map((subject) => ({
    subject,
    positions: measured.map((keyword) => {
      const found = plain
        .filter((ranking) => ranking.keyword_id === keyword.id && sameSubject(ranking.subject, subject))
        .map((ranking) => position(ranking, mode))
        .filter((value): value is number => value !== null);
      return found.length === 0 ? null : Math.min(...found);
    }),
  }));
  return { keywords: measured.map((keyword) => keyword.phrase), series };
}

/** Bar length that grows with a better position, so the leader has the longest bar. */
export function barLength(value: number | null, cutoff: number): number {
  if (value === null || value < 1 || value > cutoff) return 0;
  return cutoff + 1 - value;
}

export function positionFromBar(length: number, cutoff: number): number | null {
  return length <= 0 ? null : cutoff + 1 - length;
}

export function isCustomer(subject: string, customerName: string): boolean {
  return sameSubject(subject, customerName);
}

/** Positions that get an axis label: 1 and every fifth place, or only first, middle and last on a narrow chart. */
export function labelledPositions(cutoff: number, narrow: boolean): number[] {
  if (narrow) return [...new Set([1, Math.round(cutoff / 2), cutoff])];
  const positions = [1];
  for (let position = 5; position <= cutoff; position += 5) positions.push(position);
  return positions;
}
