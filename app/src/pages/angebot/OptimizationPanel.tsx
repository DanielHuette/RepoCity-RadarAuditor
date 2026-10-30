import type { ComponentChildren } from "preact";
import { decideChange, loadOrderWork, markManualDone, startOptimization, type OrderWork } from "../../api/offer";
import type { CrmOrder, Job, OptimizationChange } from "../../api/types";
import { CopyButton } from "../../components/CopyButton";
import { JobStatusBadge } from "../../components/JobStatusBadge";
import { ServerStatusChip } from "../../components/ServerStatusChip";
import { ErrorAlert, ErrorNote, Loading } from "../../components/ui";
import { useAction, type Action } from "../../hooks/useAsync";
import { useLive } from "../../hooks/useLive";
import { lineDiff } from "../../lib/diff";
import { formatDateTime } from "../../lib/format";
import { AREA_NAMES } from "../../lib/catalogue";
import {
  CHANGE_STATUS_LABELS,
  CHANGE_STATUS_TONES,
  groupChanges,
  isManualDone,
  isOptimizationBusy,
  latestJob,
  manualInstruction,
} from "../../lib/optimization";
import { formatTarget } from "../../lib/targets";

const DIFF_MARKS = { same: " ", removed: "−", added: "+" } as const;

function Diff({ change }: { change: OptimizationChange }) {
  return (
    <pre class="diff" aria-label="Vorher und nachher">
      {lineDiff(change.before, change.after).map((line, index) => (
        <span key={index} class={`diff__line diff__line--${line.kind}`}>
          <span class="diff__mark" aria-hidden="true">
            {DIFF_MARKS[line.kind]}
          </span>
          <span class="visually-hidden">{line.kind === "removed" ? "vorher: " : line.kind === "added" ? "nachher: " : ""}</span>
          {line.text === "" ? " " : line.text}
        </span>
      ))}
    </pre>
  );
}

function CheckMeta({ checkId }: { checkId: string | null }) {
  if (checkId === null) return null;
  const area = AREA_NAMES[checkId.replace(/\d+$/, "")];
  return (
    <span class="row__meta">
      Prüfpunkt {checkId}
      {area === undefined ? "" : ` · ${area}`}
    </span>
  );
}

function ChangeHead({ change }: { change: OptimizationChange }) {
  return (
    <div class="split">
      <span class="row__main">
        <span class="row__title">{formatTarget(change.target)}</span>
        <CheckMeta checkId={change.check_id} />
      </span>
      <span class={`badge ${CHANGE_STATUS_TONES[change.status]}`}>{CHANGE_STATUS_LABELS[change.status]}</span>
    </div>
  );
}

interface TodoLineProps {
  change: OptimizationChange;
  done: boolean;
  action: Action;
  onChanged: () => void;
}

function TodoLine({ change, done, action, onChanged }: TodoLineProps) {
  const instruction = manualInstruction(change);
  return (
    <label class="checkline todo__line">
      <input
        type="checkbox"
        checked={done}
        disabled={action.busy}
        data-write
        onChange={(event) => {
          const checked = event.currentTarget.checked;
          void action.run(async () => {
            await markManualDone(change.id, checked);
            onChanged();
          });
        }}
      />
      <span class="row__main">
        <span class="row__title todo__title">{formatTarget(change.target)}</span>
        {instruction !== null && <span class="row__meta">{instruction}</span>}
        <CheckMeta checkId={change.check_id} />
      </span>
    </label>
  );
}

function ManualTodo({ change, onChanged }: { change: OptimizationChange; onChanged: () => void }) {
  const action = useAction();
  const done = isManualDone(change);
  return (
    <li class="change todo" data-done={done ? "" : undefined}>
      <TodoLine change={change} done={done} action={action} onChanged={onChanged} />
      <ErrorAlert message={action.error} />
      {!done && (
        <details>
          <summary class="small">Neuen Inhalt ansehen</summary>
          <div class="stack stack--tight">
            <pre class="diff todo__after">{change.after}</pre>
            <CopyButton text={change.after} />
          </div>
        </details>
      )}
    </li>
  );
}

interface DecideButtonProps {
  label: string;
  approve: boolean;
  busy: boolean;
  onDecide: (approve: boolean) => void;
}

function DecideButton({ label, approve, busy, onDecide }: DecideButtonProps) {
  return (
    <button
      type="button"
      class={approve ? "btn btn--primary btn--small" : "btn btn--ghost btn--small"}
      disabled={busy}
      onClick={() => {
        onDecide(approve);
      }}
    >
      {label}
    </button>
  );
}

function DraftCard({ change, onDecided }: { change: OptimizationChange; onDecided: () => void }) {
  const action = useAction();
  const decide = (approve: boolean) =>
    void action.run(async () => {
      await decideChange(change.id, approve);
      onDecided();
    });
  return (
    <li class="change">
      <ChangeHead change={change} />
      <Diff change={change} />
      <ErrorAlert message={action.error} />
      <div class="cluster" data-write>
        <DecideButton label="Freigeben" approve busy={action.busy} onDecide={decide} />
        <DecideButton label="Ablehnen" approve={false} busy={action.busy} onDecide={decide} />
      </div>
    </li>
  );
}

function LogEntry({ change }: { change: OptimizationChange }) {
  return (
    <li class="change">
      <ChangeHead change={change} />
      <p class="row__meta">{formatDateTime(change.applied_at ?? change.decided_at)}</p>
      {change.status === "failed" && change.error !== null && <p class="field__error">{change.error}</p>}
      <details>
        <summary class="small">Änderung ansehen</summary>
        <Diff change={change} />
      </details>
    </li>
  );
}

function JobLine({ label, job }: { label: string; job: Job | null }) {
  if (job === null) return null;
  return (
    <div class="split">
      <span>
        {label} <span class="muted small">· {formatDateTime(job.updated_at)}</span>
      </span>
      <JobStatusBadge status={job.status} />
    </div>
  );
}

function StartButton({ orderId, job, onStarted }: { orderId: string; job: Job | null; onStarted: () => void }) {
  const action = useAction();
  if (job !== null && (job.status === "queued" || job.status === "running")) return null;
  return (
    <div class="stack stack--tight" data-write>
      <ErrorAlert message={action.error} />
      <button
        type="button"
        class={job === null ? "btn btn--primary btn--block" : "btn btn--ghost btn--block"}
        disabled={action.busy}
        onClick={() =>
          void action.run(async () => {
            await startOptimization(orderId);
            onStarted();
          })
        }
      >
        {job === null ? "Optimierung starten" : "Optimierung erneut starten"}
      </button>
    </div>
  );
}

function ChangeSection({ title, listClass, children }: { title: string; listClass: string; children: ComponentChildren }) {
  return (
    <div class="stack stack--tight">
      <h3>{title}</h3>
      <ul class={listClass}>{children}</ul>
    </div>
  );
}

interface ChangeListProps {
  changes: readonly OptimizationChange[];
  onChanged: () => void;
}

function Drafts({ changes, onChanged }: ChangeListProps) {
  if (changes.length === 0) return null;
  return (
    <ChangeSection title={`Zur Freigabe (${String(changes.length)})`} listClass="changes">
      {changes.map((change) => (
        <DraftCard key={change.id} change={change} onDecided={onChanged} />
      ))}
    </ChangeSection>
  );
}

function Approved({ changes }: { changes: readonly OptimizationChange[] }) {
  if (changes.length === 0) return null;
  return (
    <ChangeSection title={`Freigegeben, wird angewendet (${String(changes.length)})`} listClass="list">
      {changes.map((change) => (
        <li key={change.id} class="row">
          <span class="row__title">{formatTarget(change.target)}</span>
        </li>
      ))}
    </ChangeSection>
  );
}

function ManualTodos({ changes, onChanged }: ChangeListProps) {
  if (changes.length === 0) return null;
  const open = changes.filter((change) => !isManualDone(change)).length;
  return (
    <ChangeSection title={`Von Hand umsetzen (${String(open)} offen)`} listClass="changes">
      {changes.map((change) => (
        <ManualTodo key={change.id} change={change} onChanged={onChanged} />
      ))}
    </ChangeSection>
  );
}

function Log({ changes }: { changes: readonly OptimizationChange[] }) {
  if (changes.length === 0) return null;
  return (
    <ChangeSection title="Protokoll" listClass="changes">
      {changes.map((change) => (
        <LogEntry key={change.id} change={change} />
      ))}
    </ChangeSection>
  );
}

function ChangeGroupsView({ changes, onChanged }: ChangeListProps) {
  const groups = groupChanges(changes);
  const rejected = groups.rejected.length;
  return (
    <>
      <Drafts changes={groups.drafts} onChanged={onChanged} />
      <Approved changes={groups.approved} />
      <ManualTodos changes={groups.manual} onChanged={onChanged} />
      <Log changes={groups.log} />
      {rejected > 0 && <p class="muted small">{rejected === 1 ? "1 Entwurf abgelehnt." : `${String(rejected)} Entwürfe abgelehnt.`}</p>}
    </>
  );
}

function OptimizationWork({ orderId, work, onChanged }: { orderId: string; work: OrderWork; onChanged: () => void }) {
  const optimize = latestJob(work.jobs, "optimize");
  const busy = isOptimizationBusy(work.jobs, work.changes);
  return (
    <>
      {optimize === null && (
        <p class="muted small">
          Der Server bereitet jede Änderung an Website und Unternehmensprofil als Entwurf vor. Angewendet wird nur, was du
          freigibst.
        </p>
      )}
      <JobLine label="Entwürfe vorbereiten" job={optimize} />
      {optimize?.status === "failed" && optimize.error !== null && <p class="field__error">{optimize.error}</p>}
      <JobLine label="Freigegebene anwenden" job={latestJob(work.jobs, "apply")} />
      {busy && <ServerStatusChip offlineNote="die Optimierung läuft weiter, sobald der Server läuft." />}
      <StartButton orderId={orderId} job={optimize} onStarted={onChanged} />
      <ChangeGroupsView changes={work.changes} onChanged={onChanged} />
    </>
  );
}

export function OptimizationPanel({ order }: { order: CrmOrder }) {
  const work = useLive(
    `order-${order.id}`,
    () => loadOrderWork(order.id),
    [
      { table: "jobs", filter: `order_id=eq.${order.id}` },
      { table: "optimization_changes", filter: `order_id=eq.${order.id}` },
    ],
    (loaded) => isOptimizationBusy(loaded.jobs, loaded.changes),
  );
  return (
    <section class="panel stack" aria-labelledby="optimization-title">
      <div>
        <h2 id="optimization-title">Optimierung</h2>
        <p class="muted small">Auftrag {order.number}</p>
      </div>
      {work.data !== undefined ? (
        <OptimizationWork orderId={order.id} work={work.data} onChanged={work.reload} />
      ) : work.error === null ? (
        <Loading label="Optimierung wird geladen …" />
      ) : (
        <ErrorNote message={work.error} onRetry={work.reload} />
      )}
    </section>
  );
}
