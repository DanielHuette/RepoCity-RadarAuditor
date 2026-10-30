import type { ChartConfiguration } from "chart.js";
import { useEffect, useMemo, useRef } from "preact/hooks";
import type { Audit } from "../api/types";
import { scoreHistory, type ScorePoint } from "../lib/scoring";
import { Chart, applyChartDefaults, token } from "./chartSetup";
import { Empty } from "./ui";

function trendConfig(points: readonly ScorePoint[], gold: string): ChartConfiguration<"line"> {
  return {
    type: "line",
    data: {
      labels: points.map((point) => point.label),
      datasets: [
        {
          label: "Gesamtpunkte",
          data: points.map((point) => point.score),
          borderColor: gold,
          backgroundColor: gold,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.25,
        },
      ],
    },
    options: {
      scales: { y: { min: 0, max: 100, ticks: { stepSize: 25 } }, x: { grid: { display: false } } },
      plugins: { legend: { display: false } },
    },
  };
}

export function TrendLine({ audits }: { audits: readonly Audit[] }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const points = useMemo(() => scoreHistory(audits), [audits]);

  useEffect(() => {
    if (canvas.current === null || points.length < 2) return;
    applyChartDefaults();
    const chart = new Chart(canvas.current, trendConfig(points, token("--gold")));
    return () => {
      chart.destroy();
    };
  }, [points]);

  if (points.length < 2) {
    return <Empty>Der Verlauf erscheint ab dem zweiten bewerteten Audit.</Empty>;
  }
  return (
    <div class="chart">
      <canvas ref={canvas} role="img" aria-label="Gesamtpunkte über alle Audits" />
    </div>
  );
}
