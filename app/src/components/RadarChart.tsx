// Area scores on the radar of the public site (site/effekte/radar.js): four gold rings, a sweeping
// beam with an afterglow, gold points; the previous audit is a dashed blue outline.
import { useId } from "preact/hooks";
import type { RadarAxis } from "../lib/evaluation";
import { formatScore } from "../lib/format";

interface RadarChartProps {
  axes: readonly RadarAxis[];
  currentLabel: string;
  previousLabel: string | null;
}

const WIDTH = 340;
const HEIGHT = 300;
const CX = WIDTH / 2;
const CY = HEIGHT / 2;
const RADIUS = 80;
const LABEL_GAP = 13;
const RINGS = [1, 2, 3, 4];
// Afterglow behind the beam: like the site's shader, brightness falls with exp(-1.9 · angle).
const GLOW_STEPS = 12;
const GLOW_STEP = (4 * Math.PI) / 180;
const GLOW_OPACITY = 0.2;

interface Point {
  x: number;
  y: number;
}

function angleOf(index: number, count: number): number {
  return -Math.PI / 2 + (index * 2 * Math.PI) / count;
}

function pointAt(radius: number, angle: number): Point {
  return { x: CX + radius * Math.cos(angle), y: CY + radius * Math.sin(angle) };
}

function round(value: number): string {
  return value.toFixed(1);
}

/** An area without a measurable check sits at the centre; its label shows "–". */
function scorePoint(score: number | null, index: number, count: number): Point {
  return pointAt((RADIUS * Math.min(Math.max(score ?? 0, 0), 100)) / 100, angleOf(index, count));
}

function outline(scores: readonly (number | null)[]): string {
  return scores
    .map((score, index) => {
      const point = scorePoint(score, index, scores.length);
      return `${round(point.x)},${round(point.y)}`;
    })
    .join(" ");
}

function wedge(from: number, to: number): string {
  const start = pointAt(RADIUS, from);
  const end = pointAt(RADIUS, to);
  return `M${String(CX)},${String(CY)} L${round(start.x)},${round(start.y)} A${String(RADIUS)},${String(RADIUS)} 0 0 1 ${round(end.x)},${round(end.y)} Z`;
}

function Sweep() {
  const beam = pointAt(RADIUS, 0);
  return (
    <g class="radar__sweep">
      {Array.from({ length: GLOW_STEPS }, (_, step) => (
        <path
          key={step}
          d={wedge(-(step + 1) * GLOW_STEP, -step * GLOW_STEP)}
          fill="var(--gold)"
          fill-opacity={(GLOW_OPACITY * Math.exp(-1.9 * step * GLOW_STEP)).toFixed(3)}
        />
      ))}
      <line x1={CX} y1={CY} x2={round(beam.x)} y2={round(beam.y)} stroke="var(--gold-light)" stroke-opacity="0.55" />
    </g>
  );
}

function Grid({ count }: { count: number }) {
  return (
    <g>
      {RINGS.map((ring) => (
        <circle key={ring} cx={CX} cy={CY} r={(RADIUS * ring) / RINGS.length} fill="none" stroke="var(--gold)" stroke-opacity="0.16" />
      ))}
      {Array.from({ length: count }, (_, index) => {
        const angle = angleOf(index, count);
        const end = pointAt(RADIUS, angle);
        return (
          <g key={index}>
            <line x1={CX} y1={CY} x2={round(end.x)} y2={round(end.y)} stroke="var(--gold)" stroke-opacity="0.1" />
            {RINGS.map((ring) => {
              const dot = pointAt((RADIUS * ring) / RINGS.length, angle);
              return <circle key={ring} cx={round(dot.x)} cy={round(dot.y)} r="1.3" fill="var(--gold)" fill-opacity="0.4" />;
            })}
          </g>
        );
      })}
    </g>
  );
}

function Label({ axis, index, count }: { axis: RadarAxis; index: number; count: number }) {
  const angle = angleOf(index, count);
  const anchor = pointAt(RADIUS + LABEL_GAP, angle);
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const textAnchor = cos > 0.25 ? "start" : cos < -0.25 ? "end" : "middle";
  const nameY = sin < -0.7 ? anchor.y - 14 : sin > 0.7 ? anchor.y + 10 : anchor.y - 2;
  return (
    <text class="radar__label" x={round(anchor.x)} y={round(nameY)} text-anchor={textAnchor}>
      <tspan>{axis.label}</tspan>
      <tspan class="radar__score" x={round(anchor.x)} dy="13">
        {formatScore(axis.score)}
      </tspan>
    </text>
  );
}

function ScorePoints({ scores, axes }: { scores: readonly (number | null)[]; axes: readonly RadarAxis[] }) {
  return (
    <>
      {scores.map((score, index) => {
        const point = scorePoint(score, index, scores.length);
        return (
          <g key={axes[index]?.area ?? index}>
            <circle cx={round(point.x)} cy={round(point.y)} r="6" fill="var(--gold)" fill-opacity="0.18" />
            <circle cx={round(point.x)} cy={round(point.y)} r="2.8" fill="var(--gold)" />
          </g>
        );
      })}
    </>
  );
}

function Outlines({ axes, showPrevious }: { axes: readonly RadarAxis[]; showPrevious: boolean }) {
  return (
    <>
      {showPrevious && (
        <polygon
          points={outline(axes.map((axis) => axis.previous))}
          fill="none"
          stroke="var(--series-2)"
          stroke-width="1.4"
          stroke-dasharray="4 4"
          stroke-linejoin="round"
        />
      )}
      <polygon
        points={outline(axes.map((axis) => axis.score))}
        fill="var(--gold)"
        fill-opacity="0.16"
        stroke="var(--gold-light)"
        stroke-width="1.6"
        stroke-linejoin="round"
      />
      <ScorePoints scores={axes.map((axis) => axis.score)} axes={axes} />
    </>
  );
}

interface ValueTableProps {
  axes: readonly RadarAxis[];
  currentLabel: string;
  previousLabel: string | null;
  showPrevious: boolean;
}

/** The values of the chart for screen readers. */
function ValueTable({ axes, currentLabel, previousLabel, showPrevious }: ValueTableProps) {
  return (
    <table>
      <caption>Punkte je Bereich</caption>
      <thead>
        <tr>
          <th scope="col">Bereich</th>
          <th scope="col">{currentLabel}</th>
          {showPrevious && <th scope="col">{previousLabel}</th>}
        </tr>
      </thead>
      <tbody>
        {axes.map((axis) => (
          <tr key={axis.area}>
            <th scope="row">{axis.name}</th>
            <td>{formatScore(axis.score)}</td>
            {showPrevious && <td>{formatScore(axis.previous)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RadarChart({ axes, currentLabel, previousLabel }: RadarChartProps) {
  const titleId = useId();
  const showPrevious = previousLabel !== null && axes.some((axis) => axis.previous !== null);
  return (
    <figure class="radar">
      <svg class="radar__chart" viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`} role="img" aria-labelledby={titleId}>
        <title id={titleId}>
          Radar der Bereichspunkte{showPrevious ? " mit der vorherigen Prüfung im Vergleich" : ""}; die Tabelle darunter nennt alle Werte.
        </title>
        <Grid count={axes.length} />
        <Sweep />
        <Outlines axes={axes} showPrevious={showPrevious} />
        {axes.map((axis, index) => (
          <Label key={axis.area} axis={axis} index={index} count={axes.length} />
        ))}
      </svg>
      <figcaption class="radar__legend">
        <span class="radar__key radar__key--current">{currentLabel}</span>
        {showPrevious && <span class="radar__key radar__key--previous">{previousLabel}</span>}
      </figcaption>
      {/* Tables grow to their content, so the hiding box wraps the table. */}
      <div class="visually-hidden">
        <ValueTable axes={axes} currentLabel={currentLabel} previousLabel={previousLabel} showPrevious={showPrevious} />
      </div>
    </figure>
  );
}
