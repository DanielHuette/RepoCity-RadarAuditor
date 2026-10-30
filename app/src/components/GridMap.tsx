import { useMemo, useState } from "preact/hooks";
import type { Keyword, Ranking } from "../api/types";
import { buildGrid, type Grid } from "../lib/grid";
import { isCustomer } from "../lib/rankings";
import { ChipGroup, Empty } from "./ui";

interface GridMapProps {
  rankings: readonly Ranking[];
  keywords: readonly Keyword[];
  customerName: string;
}

const TOP = 3;
const PACK = 10;

function cellTone(position: number | null | undefined): string {
  if (position === undefined) return "gridmap__cell--empty";
  if (position === null || position > PACK) return "tone-bad";
  return position <= TOP ? "tone-ok" : "tone-warn";
}

function cellLabel(position: number | null | undefined): string {
  if (position === undefined) return "";
  return position === null ? "–" : String(position);
}

function GridCells({ grid, phrase }: { grid: Grid; phrase: string }) {
  return (
    <div
      class="gridmap"
      style={{ gridTemplateColumns: `repeat(${String(grid.size)}, minmax(0, 1fr))` }}
      role="img"
      aria-label={`Kartenplatz für „${phrase}“ an ${String(grid.size * grid.size)} Punkten rund um den Standort`}
    >
      {grid.cells.flatMap((row, rowIndex) =>
        row.map((position, columnIndex) => (
          <span key={`${String(rowIndex)}-${String(columnIndex)}`} class={`gridmap__cell ${cellTone(position)}`}>
            {cellLabel(position)}
          </span>
        )),
      )}
    </div>
  );
}

function useOwnGrid({ rankings, keywords, customerName }: GridMapProps) {
  const own = useMemo(
    () => rankings.filter((ranking) => ranking.grid_point !== null && isCustomer(ranking.subject, customerName)),
    [rankings, customerName],
  );
  const measured = keywords.filter((keyword) => own.some((ranking) => ranking.keyword_id === keyword.id));
  const [keywordId, setKeywordId] = useState<string | null>(null);
  const selected = measured.find((keyword) => keyword.id === keywordId) ?? measured[0];
  const grid = useMemo(
    () => (selected === undefined ? null : buildGrid(own.filter((ranking) => ranking.keyword_id === selected.id))),
    [own, selected],
  );
  return { measured, selected, grid, setKeywordId };
}

export function GridMap(props: GridMapProps) {
  const { measured, selected, grid, setKeywordId } = useOwnGrid(props);
  if (selected === undefined || grid === null) {
    return <Empty>Kein Kartenraster gemessen. Das Raster braucht den Bezahldienst für Platzierungen.</Empty>;
  }
  const phrases = new Map(measured.map((keyword) => [keyword.id, keyword.phrase]));
  return (
    <div class="stack">
      {measured.length > 1 && (
        <ChipGroup
          label="Suchbegriff"
          values={[...phrases.keys()]}
          selected={selected.id}
          labelOf={(id) => phrases.get(id) ?? id}
          onSelect={setKeywordId}
        />
      )}
      <GridCells grid={grid} phrase={selected.phrase} />
      <p class="muted small">Zahl = Platz in der Kartenansicht an diesem Punkt. Strich = nicht unter den ersten {PACK}.</p>
    </div>
  );
}
