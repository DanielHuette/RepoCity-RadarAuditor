import type { Ranking } from "../api/types";

export interface GridCell {
  row: number;
  column: number;
}

export interface Grid {
  size: number;
  cells: (number | null | undefined)[][];
}

const GRID_POINT = /^r(\d+)c(\d+)$/i;

export function parseGridPoint(value: string): GridCell | null {
  const match = GRID_POINT.exec(value.trim());
  if (match === null) return null;
  return { row: Number(match[1]), column: Number(match[2]) };
}

/**
 * Square grid of local pack positions. Indices may start at 0 or 1; the grid is anchored at
 * the smallest index seen. `undefined` marks a point that was not measured, `null` a point
 * where the business was not listed.
 */
export function buildGrid(rankings: readonly Ranking[]): Grid | null {
  const points = rankings.flatMap((ranking) => {
    const cell = ranking.grid_point === null ? null : parseGridPoint(ranking.grid_point);
    return cell === null ? [] : [{ ...cell, position: ranking.local_pack_position }];
  });
  if (points.length === 0) return null;
  const base = Math.min(1, ...points.map((point) => Math.min(point.row, point.column)));
  const size = Math.max(...points.map((point) => Math.max(point.row, point.column))) - base + 1;
  const cells: (number | null | undefined)[][] = Array.from({ length: size }, () =>
    Array.from({ length: size }, () => undefined),
  );
  for (const point of points) {
    const row = cells[point.row - base];
    if (row === undefined) continue;
    const current = row[point.column - base];
    const next = point.position;
    if (current === undefined || current === null) row[point.column - base] = next;
    else if (next !== null) row[point.column - base] = Math.min(current, next);
  }
  return { size, cells };
}
