import { useId } from "preact/hooks";
import { formatScore } from "../lib/format";

interface ScoreRingProps {
  score: number | null;
  size?: number;
  label?: string;
}

const STROKE = 10;

function Ring({ size, share }: { size: number; share: number }) {
  const radius = (size - STROKE) / 2;
  const circumference = 2 * Math.PI * radius;
  const centre = size / 2;
  const gradient = `score-ring-${useId()}`;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${String(size)} ${String(size)}`} aria-hidden="true">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "var(--gold-light)" }} />
          <stop offset="1" style={{ stopColor: "var(--gold-deep)" }} />
        </linearGradient>
      </defs>
      <circle cx={centre} cy={centre} r={radius} fill="none" style={{ stroke: "var(--chart-line)" }} stroke-width={STROKE} />
      <circle
        cx={centre}
        cy={centre}
        r={radius}
        fill="none"
        stroke={`url(#${gradient})`}
        stroke-width={STROKE}
        stroke-linecap="round"
        stroke-dasharray={`${String(circumference * share)} ${String(circumference)}`}
        transform={`rotate(-90 ${String(centre)} ${String(centre)})`}
      />
    </svg>
  );
}

export function ScoreRing({ score, size = 180, label = "von 100" }: ScoreRingProps) {
  const share = score === null ? 0 : Math.min(Math.max(score, 0), 100) / 100;
  return (
    <div class="score-ring" role="img" aria-label={score === null ? "Noch keine Punkte" : `${formatScore(score)} von 100 Punkten`}>
      <Ring size={size} share={share} />
      <span class="score-ring__value">
        <span class="score-ring__number">{formatScore(score)}</span>
        <span class="score-ring__label">{label}</span>
      </span>
    </div>
  );
}
