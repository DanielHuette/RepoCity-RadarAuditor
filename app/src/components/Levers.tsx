import { checkAnchor, formatLeverPoints, type Lever } from "../lib/evaluation";

/** Opens the check in "Alle Prüfungen" and brings it into view. */
function showCheck(checkId: string): void {
  const row = document.getElementById(checkAnchor(checkId));
  if (row === null) return;
  const details = row.querySelector("details");
  if (details !== null) details.open = true;
  row.scrollIntoView({ behavior: "smooth", block: "start" });
  row.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
}

function LeverButton({ lever, rank }: { lever: Lever; rank: number }) {
  return (
    <button
      type="button"
      class="lever"
      onClick={() => {
        showCheck(lever.checkId);
      }}
    >
      <span class="lever__rank" aria-hidden="true">
        {rank}
      </span>
      <span class="lever__text">
        <span class="lever__name">{lever.name}</span>
        {lever.measured !== null && <span class="lever__measured">{lever.measured}</span>}
      </span>
      <span class="lever__points num" aria-label={`${formatLeverPoints(lever.potential)} Punkte möglich`}>
        {formatLeverPoints(lever.potential)}
      </span>
    </button>
  );
}

/** The three checks with the most open potential; a tap jumps to the check below. */
export function Levers({ levers }: { levers: readonly Lever[] }) {
  if (levers.length === 0) return null;
  return (
    <section class="panel levers" aria-labelledby="levers-title">
      <h2 id="levers-title">Die größten Hebel</h2>
      <ol class="levers__list">
        {levers.map((lever, index) => (
          <li key={lever.checkId}>
            <LeverButton lever={lever} rank={index + 1} />
          </li>
        ))}
      </ol>
    </section>
  );
}
