import { useId } from "preact/hooks";
import mark from "../brand/mark.json";

interface MarkProps {
  size?: number;
}

function BrassGradient({ id }: { id: string }) {
  return (
    <defs>
      <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2={mark.viewBox} y2={mark.viewBox}>
        <stop offset="0" stop-color={mark.brass.light} />
        <stop offset="0.45" stop-color={mark.brass.base} />
        <stop offset="1" stop-color={mark.brass.deep} />
      </linearGradient>
    </defs>
  );
}

function LogoMark({ size = 40 }: MarkProps) {
  const gradient = `brass-${useId()}`;
  const stroke = `url(#${gradient})`;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${String(mark.viewBox)} ${String(mark.viewBox)}`}
      aria-hidden="true"
      focusable="false"
    >
      <BrassGradient id={gradient} />
      <circle cx={mark.circle.cx} cy={mark.circle.cy} r={mark.circle.r} fill="none" stroke={stroke} stroke-width={mark.circle.stroke} />
      <line
        x1={mark.horizon.x1}
        x2={mark.horizon.x2}
        y1={mark.horizon.y}
        y2={mark.horizon.y}
        stroke={stroke}
        stroke-width={mark.horizon.stroke}
        stroke-linecap="round"
      />
      {mark.bars.map((bar) => (
        <rect key={bar.x} x={bar.x} y={bar.y} width={bar.width} height={bar.height} rx={0.8} fill={stroke} />
      ))}
    </svg>
  );
}

interface LogoProps {
  size?: number;
  compact?: boolean;
}

export function Logo({ size = 40, compact = false }: LogoProps) {
  return (
    <span class="logo" role="img" aria-label="RepoCity – AI for everyone">
      <LogoMark size={size} />
      {!compact && (
        <span class="logo__words">
          <span class="logo__name">REPOCITY</span>
          <span class="logo__line">AI FOR EVERYONE</span>
        </span>
      )}
    </span>
  );
}
