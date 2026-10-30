import type { RefObject } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { ChartOptions } from "chart.js";
import type { Keyword, Ranking } from "../api/types";
import {
  POSITION_CUTOFF,
  barLength,
  isCustomer,
  labelledPositions,
  positionFromBar,
  rankingMatrix,
  type RankingMatrix,
  type RankingMode,
} from "../lib/rankings";
import { Chart, applyChartDefaults, token } from "./chartSetup";
import { ChipGroup, Empty } from "./ui";

interface CompetitorBarsProps {
  rankings: readonly Ranking[];
  keywords: readonly Keyword[];
  customerName: string;
}

const SERIES_TOKENS = ["--series-2", "--series-3", "--series-4", "--series-5"];
const ROW_HEIGHT = 28;
const MIN_HEIGHT = 240;
const NARROW_CHART_PX = 400;

const MODE_LABELS: Record<RankingMode, string> = { organic: "Google-Suche", local: "Karte" };
const MODES = Object.keys(MODE_LABELS) as RankingMode[];

function tickLabel(value: number | string, cutoff: number, chartWidth: number): string {
  const position = positionFromBar(Number(value), cutoff);
  const labelled = labelledPositions(cutoff, chartWidth < NARROW_CHART_PX);
  return position !== null && labelled.includes(position) ? String(position) : "";
}

function tooltipLabel(raw: unknown, series: string | undefined, cutoff: number): string {
  const position = positionFromBar(Number(raw), cutoff);
  const place = position === null ? `nicht unter den ersten ${String(cutoff)}` : `Platz ${String(position)}`;
  return `${series ?? ""}: ${place}`;
}

function barOptions(cutoff: number): ChartOptions<"bar"> {
  return {
    indexAxis: "y",
    scales: {
      x: {
        min: 0,
        max: cutoff,
        title: { display: true, text: "Platz" },
        ticks: {
          stepSize: 1,
          autoSkip: false,
          maxRotation: 0,
          callback(value) {
            return tickLabel(value, cutoff, this.chart.width);
          },
        },
      },
      y: { grid: { display: false } },
    },
    plugins: {
      legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12 } },
      tooltip: { callbacks: { label: (item) => tooltipLabel(item.raw, item.dataset.label, cutoff) } },
    },
  };
}

function barDatasets(matrix: RankingMatrix, cutoff: number, customerName: string) {
  let competitorIndex = 0;
  return matrix.series.map((series) => {
    const own = isCustomer(series.subject, customerName);
    const color = own ? token("--gold") : token(SERIES_TOKENS[competitorIndex++ % SERIES_TOKENS.length] ?? "--series-4");
    return {
      label: own ? `${series.subject} (Kunde)` : series.subject,
      data: series.positions.map((position) => barLength(position, cutoff)),
      backgroundColor: color,
      borderRadius: 4,
      barPercentage: 0.9,
      categoryPercentage: 0.8,
    };
  });
}

function useBarChart(canvas: RefObject<HTMLCanvasElement>, matrix: RankingMatrix, cutoff: number, customerName: string): void {
  useEffect(() => {
    if (canvas.current === null || matrix.keywords.length === 0) return;
    applyChartDefaults();
    const datasets = barDatasets(matrix, cutoff, customerName);
    const chart = new Chart(canvas.current, { type: "bar", data: { labels: matrix.keywords, datasets }, options: barOptions(cutoff) });
    return () => {
      chart.destroy();
    };
  }, [matrix, cutoff, customerName]);
}

export function CompetitorBars({ rankings, keywords, customerName }: CompetitorBarsProps) {
  const [mode, setMode] = useState<RankingMode>("organic");
  const canvas = useRef<HTMLCanvasElement>(null);
  const matrix = useMemo(() => rankingMatrix(rankings, keywords, customerName, mode), [rankings, keywords, customerName, mode]);
  const cutoff = POSITION_CUTOFF[mode];
  useBarChart(canvas, matrix, cutoff, customerName);
  const height = Math.max(MIN_HEIGHT, matrix.keywords.length * matrix.series.length * ROW_HEIGHT + 80);

  return (
    <div class="stack">
      <ChipGroup label="Art der Platzierung" values={MODES} selected={mode} labelOf={(key) => MODE_LABELS[key]} onSelect={setMode} />
      {matrix.keywords.length === 0 ? (
        <Empty>Für dieses Audit gibt es noch keine Platzierungen.</Empty>
      ) : (
        <div class="chart" style={{ height: `${String(height)}px` }}>
          <canvas ref={canvas} role="img" aria-label={`Platzierungen je Suchbegriff, ${MODE_LABELS[mode]}`} />
        </div>
      )}
      <p class="muted small">Längerer Balken heißt besserer Platz. Fehlt ein Balken, steht der Betrieb nicht unter den ersten {cutoff}.</p>
    </div>
  );
}
