import type { AreaSummary } from "../lib/scoring";
import { formatScore } from "../lib/format";

export function AreaTrafficLights({ areas }: { areas: readonly AreaSummary[] }) {
  return (
    <ul class="areas">
      {areas.map((area) => {
        const tone = area.tone === "none" ? "" : `tone-${area.tone}`;
        const measured = area.counts.passed + area.counts.partial + area.counts.failed;
        return (
          <li key={area.area} class="area">
            <span class={`area__light ${tone}`} aria-hidden="true" />
            <span class="area__name">
              <span>{area.name}</span>
              <span class={`area__bar ${tone}`} aria-hidden="true">
                <span class="area__fill" style={{ width: `${String(area.score ?? 0)}%` }} />
              </span>
              <span class="muted small">
                {measured === 0
                  ? "Nicht messbar"
                  : `${String(area.counts.passed)} bestanden · ${String(area.counts.partial)} teilweise · ${String(area.counts.failed)} offen`}
              </span>
            </span>
            <span class={`area__score ${tone}`}>{formatScore(area.score)}</span>
          </li>
        );
      })}
    </ul>
  );
}
