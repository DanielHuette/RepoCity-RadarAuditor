import type { AuditWithCustomer } from "../api/types";
import { formatDateTime, formatScore } from "../lib/format";
import { PROGRESS_LABELS, PROGRESS_TONES, stepOfAuditStatus } from "../lib/progress";
import { Empty } from "./ui";

const toAuswertung = (audit: AuditWithCustomer) => `/auswertung/${audit.id}`;

function AuditRow({ audit, href }: { audit: AuditWithCustomer; href: string }) {
  const step = stepOfAuditStatus(audit.status);
  return (
    <li>
      <a class="row" href={href}>
        <span class="row__main">
          <span class="row__title">{audit.customers?.name ?? "Unbekannter Betrieb"}</span>
          <span class="row__meta">{formatDateTime(audit.requested_at)}</span>
        </span>
        <span class="row__side">
          <span class={`badge ${PROGRESS_TONES[step]}`}>{PROGRESS_LABELS[step]}</span>
          <span class="score-chip" aria-label={`Punkte: ${formatScore(audit.total_score)}`}>
            {formatScore(audit.total_score)}
          </span>
        </span>
      </a>
    </li>
  );
}

export function RecentAudits({
  audits,
  hrefFor = toAuswertung,
}: {
  audits: readonly AuditWithCustomer[];
  hrefFor?: (audit: AuditWithCustomer) => string;
}) {
  if (audits.length === 0) return <Empty>Noch keine Prüfung. Starte die erste oben.</Empty>;
  return (
    <ul class="list">
      {audits.map((audit) => (
        <AuditRow key={audit.id} audit={audit} href={hrefFor(audit)} />
      ))}
    </ul>
  );
}
