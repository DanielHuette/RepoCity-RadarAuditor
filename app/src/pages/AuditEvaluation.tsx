import { useLocation } from "preact-iso";
import { useState } from "preact/hooks";
import { auditChecks, auditResults, auditTexts, getAudit, getCustomer, listAudits, listKeywords, listRankings, reportUrl } from "../api/queries";
import type { Audit, AuditWithCustomer, CheckResult, Customer, Finding } from "../api/types";
import { AreaTrafficLights } from "../components/AreaTrafficLights";
import { CheckRow } from "../components/CheckRow";
import { CompetitorBars } from "../components/CompetitorBars";
import { CrmButton } from "../components/CrmButton";
import { FindingCard } from "../components/FindingCard";
import { GridMap } from "../components/GridMap";
import { IdentityBar } from "../components/IdentityBar";
import { JobStatusBadge } from "../components/JobStatusBadge";
import { Levers } from "../components/Levers";
import { openInNewTab } from "../components/openUrl";
import { RadarChart } from "../components/RadarChart";
import { ReviewReplies } from "../components/ReviewReplies";
import { ScoreRing } from "../components/ScoreRing";
import { TalkBox } from "../components/TalkBox";
import { TextSuggestions } from "../components/TextSuggestions";
import { TrendLine } from "../components/TrendLine";
import { ChipGroup, Empty, ErrorNote, Loaded, Select, Toggle } from "../components/ui";
import { useAction, useAsync } from "../hooks/useAsync";
import {
  buildRows,
  formatPotential,
  formatUplift,
  groupRows,
  orphanFindings,
  potentialTotals,
  previousAudit,
  radarAxes,
  SORT_LABELS,
  topLevers,
  type CheckRow as Row,
  type PotentialTotals,
  type SortMode,
} from "../lib/evaluation";
import { formatDate, formatDateTime, formatScore } from "../lib/format";
import { AUDIT_KIND_LABELS, AUDIT_STATUS_LABELS } from "../lib/labels";
import { groupByArea, sortFindings } from "../lib/scoring";

const SWITCHER_AUDITS = 10;
const SORT_MODES: readonly SortMode[] = ["potential", "area", "urgency"];

async function loadAudit(id: string) {
  const audit = await getAudit(id);
  if (audit === null) return null;
  const [results, rankings, keywords, history, customer] = await Promise.all([
    auditResults(id),
    listRankings(id),
    listKeywords(audit.customer_id),
    listAudits(audit.customer_id),
    getCustomer(audit.customer_id),
  ]);
  const previous = previousAudit(history, audit);
  const previousChecks = previous === null ? null : await auditChecks(previous.id);
  return { audit, customer, ...results, rankings, keywords, history, previous, previousChecks };
}

function statusHint(audit: AuditWithCustomer): string | null {
  switch (audit.status) {
    case "requested":
      return "Wartet darauf, dass der Server den Auftrag abholt.";
    case "collecting":
    case "collected":
    case "scored":
      return "Der Server arbeitet. Ergebnisse erscheinen hier, sobald sie da sind.";
    case "awaiting_analysis":
      return "Die Befunde werden gerade geschrieben.";
    case "analysed":
      return "Befunde sind da, der Bericht wird gerade erstellt.";
    case "failed":
      return audit.error === null ? "Die Prüfung ist fehlgeschlagen." : `Fehlgeschlagen: ${audit.error}`;
    case "reported":
      return null;
  }
}

function auditOption(audit: Audit): string {
  const result = audit.total_score === null ? AUDIT_STATUS_LABELS[audit.status] : `${formatScore(audit.total_score)} Punkte`;
  return `${formatDate(audit.requested_at)} · ${result}`;
}

function AuditSwitcher({ current, history }: { current: Audit; history: readonly Audit[] }) {
  const { route } = useLocation();
  const latest = history.slice(0, SWITCHER_AUDITS);
  const audits = latest.some((audit) => audit.id === current.id) ? latest : [current, ...latest];
  if (audits.length < 2) return null;
  return (
    <Select
      label="Prüfung wechseln"
      value={current.id}
      options={audits.map((audit) => ({ value: audit.id, label: auditOption(audit) }))}
      onChange={(id) => {
        route(`/auswertung/${id}`);
      }}
    />
  );
}

function PotentialSummary({ totals }: { totals: PotentialTotals }) {
  const uplift = formatUplift(totals.upliftLow, totals.upliftHigh);
  return (
    <section class="panel potential" aria-labelledby="potential-title">
      <h2 id="potential-title">Offenes Potenzial</h2>
      {!totals.known ? (
        <p class="muted">Für diese Prüfung ist noch kein Potenzial berechnet.</p>
      ) : totals.openChecks === 0 ? (
        <p>Alle messbaren Prüfungen sind bestanden.</p>
      ) : (
        <>
          <p class="potential__points">{formatPotential(totals.points)}</p>
          {uplift !== null && <p class="potential__uplift">{uplift}</p>}
          <p class="muted small">
            aus {String(totals.openChecks)} {totals.openChecks === 1 ? "offenen Prüfung" : "offenen Prüfungen"}
          </p>
        </>
      )}
    </section>
  );
}

function CheckGroups({ groups }: { groups: ReturnType<typeof groupRows> }) {
  return (
    <>
      {groups.map((group) => (
        <section key={group.area} class="panel check-group" aria-label={group.name}>
          <div class="check-group__head">
            <h3>{group.name}</h3>
            {group.potential > 0 && <span class="check__points">{formatPotential(group.potential)}</span>}
          </div>
          <ul class="checks">
            {group.rows.map((row) => (
              <CheckRow key={row.check.id} row={row} />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

function CheckList({ rows }: { rows: readonly Row[] }) {
  const [mode, setMode] = useState<SortMode>("potential");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const groups = groupRows(rows, mode, onlyOpen);
  return (
    <section class="stack" aria-labelledby="checks-title">
      <h2 id="checks-title">Alle Prüfungen</h2>
      <div class="stack stack--tight">
        <ChipGroup
          label="Sortieren nach"
          values={SORT_MODES}
          selected={mode}
          labelOf={(option) => SORT_LABELS[option]}
          onSelect={setMode}
        />
        <Toggle label="Nur offenes Potenzial" checked={onlyOpen} onChange={setOnlyOpen} />
      </div>
      {groups.length === 0 ? (
        <div class="panel">
          <Empty>{rows.length === 0 ? "Noch keine Prüfergebnisse." : "Kein offenes Potenzial."}</Empty>
        </div>
      ) : (
        <CheckGroups groups={groups} />
      )}
    </section>
  );
}

function ReportButton({ audit }: { audit: AuditWithCustomer }) {
  const opening = useAction();
  if (audit.report_path === null) return null;
  return (
    <div class="stack stack--tight">
      <button
        type="button"
        class="btn btn--primary btn--long"
        disabled={opening.busy}
        onClick={() =>
          void opening.run(async () => {
            await openInNewTab(() => reportUrl(audit));
          })
        }
      >
        Bericht öffnen (PDF)
      </button>
      {opening.error !== null && <p class="field__error">{opening.error}</p>}
    </div>
  );
}

function ScoreSection({ checks, previous, previousChecks, audit }: {
  checks: readonly CheckResult[];
  previous: Audit | null;
  previousChecks: readonly CheckResult[] | null;
  audit: Audit;
}) {
  const axes = radarAxes(checks, previous === null ? null : previousChecks);
  return (
    <div class="score-layout">
      <section class="panel stack" aria-labelledby="total">
        <h2 id="total">Gesamtpunkte</h2>
        <ScoreRing score={audit.total_score} />
        {checks.length > 0 && (
          <RadarChart
            axes={axes}
            currentLabel={`Diese Prüfung, ${formatDate(audit.requested_at)}`}
            previousLabel={previous === null ? null : `Vorherige, ${formatDate(previous.requested_at)}`}
          />
        )}
      </section>
      <section class="panel" aria-labelledby="areas">
        <h2 id="areas">Bereiche</h2>
        {checks.length === 0 ? <Empty>Noch keine Prüfergebnisse.</Empty> : <AreaTrafficLights areas={groupByArea(checks)} />}
      </section>
    </div>
  );
}

type LoadedAudit = NonNullable<Awaited<ReturnType<typeof loadAudit>>>;

function TextsError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section class="panel" aria-label="Texte zur Prüfung">
      <ErrorNote message={message} onRetry={onRetry} />
    </section>
  );
}

function EvaluationHead({ audit, customer, customerName }: { audit: AuditWithCustomer; customer: Customer | null; customerName: string }) {
  return (
    <header class="page-head">
      <div>
        <a class="back" href="/auswertung">
          ← Alle Prüfungen
        </a>
        <h1>{customerName || AUDIT_KIND_LABELS[audit.kind]}</h1>
        <p class="muted">
          {AUDIT_KIND_LABELS[audit.kind]} · {formatDateTime(audit.requested_at)}
        </p>
      </div>
      <div class="cluster">
        <JobStatusBadge status={audit.status} />
        {customer !== null && <CrmButton crmCustomerId={customer.crm_customer_id} />}
      </div>
    </header>
  );
}

function OrphanFindings({ checks, findings }: { checks: readonly CheckResult[]; findings: readonly Finding[] }) {
  const orphans = sortFindings(orphanFindings(checks, findings));
  if (orphans.length === 0) return null;
  return (
    <section class="stack" aria-labelledby="orphans">
      <h2 id="orphans">Weitere Befunde</h2>
      {orphans.map((finding) => (
        <FindingCard key={finding.id} finding={finding} />
      ))}
    </section>
  );
}

function RankingSections({ loaded, customerName }: { loaded: LoadedAudit; customerName: string }) {
  const { rankings, keywords, history } = loaded;
  return (
    <>
      <div class="grid-2">
        <section class="panel" aria-labelledby="competitors">
          <h2 id="competitors">Platzierungen im Vergleich</h2>
          <CompetitorBars rankings={rankings} keywords={keywords} customerName={customerName} />
        </section>
        <section class="panel" aria-labelledby="grid">
          <h2 id="grid">Kartenraster</h2>
          <GridMap rankings={rankings} keywords={keywords} customerName={customerName} />
        </section>
      </div>

      <section class="panel" aria-labelledby="trend">
        <h2 id="trend">Verlauf</h2>
        <TrendLine audits={history} />
      </section>
    </>
  );
}

function EvaluationView({ loaded }: { loaded: LoadedAudit }) {
  const { audit, customer, checks, findings, history, previous, previousChecks } = loaded;
  const texts = useAsync(() => auditTexts(audit.id), [audit.id]);
  const customerName = audit.customers?.name ?? "";
  const hint = statusHint(audit);
  const talk = texts.data?.talk ?? null;
  return (
    <div class="stack">
      <EvaluationHead audit={audit} customer={customer} customerName={customerName} />
      {customer !== null && <IdentityBar customer={customer} audit={audit} />}
      {texts.error !== null && <TextsError message={texts.error} onRetry={texts.reload} />}
      {talk !== null && <TalkBox text={talk} />}
      <Levers levers={topLevers(checks, findings)} />
      <div class="evaluation-tools">
        <AuditSwitcher current={audit} history={history} />
        <ReportButton audit={audit} />
      </div>
      {hint !== null && <p class={audit.status === "failed" ? "notice notice--bad" : "notice"}>{hint}</p>}
      {customer !== null && <ReviewReplies auditId={audit.id} auditStatus={audit.status} customerId={customer.id} />}

      <PotentialSummary totals={potentialTotals(checks)} />
      <ScoreSection checks={checks} previous={previous} previousChecks={previousChecks} audit={audit} />
      <CheckList rows={buildRows(checks, findings)} />
      <OrphanFindings checks={checks} findings={findings} />
      {texts.data !== undefined && texts.data !== null && <TextSuggestions texts={texts.data} />}
      <RankingSections loaded={loaded} customerName={customerName} />
    </div>
  );
}

export function AuditEvaluation({ id }: { id: string }) {
  const state = useAsync(() => loadAudit(id), [id]);
  return (
    <Loaded state={state} label="Prüfung wird geladen …">
      {(loaded) => (loaded === null ? <ErrorNote message="Diese Prüfung gibt es nicht mehr." /> : <EvaluationView loaded={loaded} />)}
    </Loaded>
  );
}
