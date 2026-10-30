import { useAuditProgress } from "../hooks/auditProgress";
import { formatDateTime } from "../lib/format";
import { analysisWaitText, PROGRESS_LABELS, PROGRESS_STEPS, type Progress } from "../lib/progress";
import { ErrorNote, Loading } from "./ui";

function stepState(index: number, current: number): "past" | "current" | "next" {
  if (index < current) return "past";
  return index === current ? "current" : "next";
}

function Steps({ progress }: { progress: Progress }) {
  const current = PROGRESS_STEPS.indexOf(progress.step);
  return (
    <ol class="steps" aria-label="Fortschritt">
      {PROGRESS_STEPS.map((step, index) => {
        const state = stepState(index, current);
        return (
          <li key={step} data-state={state} aria-current={state === "current" ? "step" : undefined}>
            {PROGRESS_LABELS[step]}
          </li>
        );
      })}
    </ol>
  );
}

function ProgressState({ progress }: { progress: Progress }) {
  return (
    <>
      {progress.step === "failed" ? (
        <p class="notice notice--bad" role="alert">
          <strong>{PROGRESS_LABELS.failed}:</strong> {progress.error ?? "Die Prüfung ist fehlgeschlagen."}
        </p>
      ) : (
        <Steps progress={progress} />
      )}
      {progress.serpRetry && (
        <p class="notice" role="status">
          Google-Suche wird nachgeholt. Die Prüfung läuft weiter und wird neu bewertet, sobald die Suche durch ist.
        </p>
      )}
      {progress.analysisWait !== null && (
        <p class="notice" role="status">
          {analysisWaitText(progress.analysisWait, new Date())}
        </p>
      )}
    </>
  );
}

function ProgressActions({ auditId, done, onNew }: { auditId: string; done: boolean; onNew: () => void }) {
  return (
    <div class="cluster progress__actions">
      {done && (
        <a class="btn btn--primary btn--main btn--long" href={`/auswertung/${auditId}`}>
          Auswertung ansehen
        </a>
      )}
      <button type="button" class="btn btn--ghost" data-write onClick={onNew}>
        Neue Prüfung
      </button>
    </div>
  );
}

export function AuditProgress({ auditId, onNew }: { auditId: string; onNew: () => void }) {
  const { audit, progress, error } = useAuditProgress(auditId);

  if (audit === undefined) return error === null ? <Loading label="Prüfung wird geladen …" /> : <ErrorNote message={error} />;
  if (audit === null || progress === null) return <ErrorNote message="Diese Prüfung gibt es nicht mehr." />;

  return (
    <section class="panel stack progress" aria-labelledby="progress-title" aria-live="polite">
      <div>
        <h2 id="progress-title">{audit.customers?.name ?? "Prüfung"}</h2>
        <p class="muted small">gestartet {formatDateTime(audit.requested_at)}</p>
      </div>
      <ProgressState progress={progress} />
      {error !== null && <p class="field__error">{error}</p>}
      <ProgressActions auditId={audit.id} done={progress.step === "done"} onNew={onNew} />
    </section>
  );
}
