import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";

Chart.register(BarController, BarElement, CategoryScale, Legend, LinearScale, LineController, LineElement, PointElement, Tooltip);

/** Matches --chart-line in theme/tokens.css; used only where the token cannot be read. */
const FALLBACK_CHART_LINE = "rgba(243, 241, 234, 0.08)";

export function token(name: string, fallback = ""): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value === "" ? fallback : value;
}

export function applyChartDefaults(): void {
  Chart.defaults.color = token("--text-muted");
  Chart.defaults.borderColor = token("--chart-line", FALLBACK_CHART_LINE);
  Chart.defaults.font.family = token("--font-body");
  Chart.defaults.maintainAspectRatio = false;
}

export { Chart };
