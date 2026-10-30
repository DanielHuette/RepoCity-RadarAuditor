// Progress of one audit as the Prüfen page shows it, derived from the audit row and its job rows.
import type { AuditStatus, JobKind, JobStatus } from "../api/types";

export type ProgressStep = "waiting" | "collecting" | "scoring" | "analysing" | "done" | "failed";

export const PROGRESS_STEPS: readonly ProgressStep[] = ["waiting", "collecting", "scoring", "analysing", "done"];

export const PROGRESS_LABELS: Record<ProgressStep, string> = {
  waiting: "Wartet",
  collecting: "Sammelt",
  scoring: "Bewertet",
  analysing: "Wertet aus",
  done: "Fertig",
  failed: "Fehler",
};

export const PROGRESS_TONES: Record<ProgressStep, string> = {
  waiting: "",
  collecting: "tone-busy",
  scoring: "tone-busy",
  analysing: "tone-busy",
  done: "tone-ok",
  failed: "tone-bad",
};

export interface ProgressAudit {
  status: AuditStatus;
  error: string | null;
}

export interface ProgressJob {
  kind: JobKind;
  status: JobStatus;
  error: string | null;
  created_at: string;
  run_after: string | null;
}

/** The analyse job got no answer from Claude Code and waits for its next attempt. */
export interface AnalysisWait {
  reason: string;
  nextAttempt: string | null;
}

export interface Progress {
  step: ProgressStep;
  /** A Google search was blocked and is queued again; the audit goes on meanwhile. */
  serpRetry: boolean;
  analysisWait: AnalysisWait | null;
  error: string | null;
}

const AUDIT_STEPS: Record<AuditStatus, ProgressStep> = {
  requested: "waiting",
  collecting: "collecting",
  collected: "scoring",
  scored: "analysing",
  awaiting_analysis: "analysing",
  analysed: "analysing",
  reported: "done",
  failed: "failed",
};

const JOB_STEPS: Partial<Record<JobKind, ProgressStep>> = {
  score: "scoring",
  analyse: "analysing",
  report: "analysing",
};

export function stepOfAuditStatus(status: AuditStatus): ProgressStep {
  return AUDIT_STEPS[status];
}

export function isFinished(step: ProgressStep): boolean {
  return step === "done" || step === "failed";
}

function stepOfJob(job: ProgressJob): ProgressStep {
  if (job.status === "done" || job.status === "failed") return "waiting";
  if (job.kind === "collect") return job.status === "running" ? "collecting" : "waiting";
  return JOB_STEPS[job.kind] ?? "waiting";
}

function newestPerKind(jobs: readonly ProgressJob[]): ProgressJob[] {
  const newest = new Map<JobKind, ProgressJob>();
  for (const job of jobs) {
    const known = newest.get(job.kind);
    if (known === undefined || job.created_at >= known.created_at) newest.set(job.kind, job);
  }
  return [...newest.values()];
}

function later(first: ProgressStep, second: ProgressStep): ProgressStep {
  return PROGRESS_STEPS.indexOf(second) > PROGRESS_STEPS.indexOf(first) ? second : first;
}

// The worker re-queues an analyse job with the German reason in error and the next attempt in run_after
// (src/radarauditor/jobs/worker.py); while it runs again the old reason is stale and not shown.
function analysisWaitOf(audit: ProgressAudit, current: readonly ProgressJob[]): AnalysisWait | null {
  if (audit.status !== "awaiting_analysis") return null;
  const analyse = current.find((job) => job.kind === "analyse");
  if (analyse?.status !== "queued" || analyse.error === null || analyse.error.trim() === "") return null;
  return { reason: analyse.error.trim(), nextAttempt: analyse.run_after };
}

export function auditProgress(audit: ProgressAudit, jobs: readonly ProgressJob[]): Progress {
  const current = newestPerKind(jobs);
  const serpRetry = current.some((job) => job.kind === "serp_retry" && (job.status === "queued" || job.status === "running"));
  const auditStep = stepOfAuditStatus(audit.status);
  if (auditStep === "failed") return { step: "failed", serpRetry: false, analysisWait: null, error: audit.error };
  if (auditStep === "done") return { step: "done", serpRetry, analysisWait: null, error: null };

  const failedJob = current.find((job) => job.status === "failed" && job.kind !== "serp_retry");
  if (failedJob !== undefined) {
    return { step: "failed", serpRetry: false, analysisWait: null, error: failedJob.error ?? audit.error };
  }

  const step = current.map(stepOfJob).reduce(later, auditStep);
  return { step, serpRetry, analysisWait: analysisWaitOf(audit, current), error: null };
}

const clock = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayMonth = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" });

function sameDay(first: Date, second: Date): boolean {
  return first.toDateString() === second.toDateString();
}

/** "um 14:30 Uhr" today, "am 24.10. um 09:00 Uhr" on another day, "gleich" when due; null without a time. */
export function formatNextAttempt(runAfter: string | null, now: Date): string | null {
  if (runAfter === null) return null;
  const at = new Date(runAfter);
  if (Number.isNaN(at.getTime())) return null;
  if (at.getTime() <= now.getTime()) return "gleich";
  const time = `um ${clock.format(at)} Uhr`;
  return sameDay(at, now) ? time : `am ${dayMonth.format(at)} ${time}`;
}

/** "Auswertung wartet: <Grund> – nächster Versuch um 14:30 Uhr". */
export function analysisWaitText(wait: AnalysisWait, now: Date): string {
  const next = formatNextAttempt(wait.nextAttempt, now);
  return next === null ? `Auswertung wartet: ${wait.reason}` : `Auswertung wartet: ${wait.reason} – nächster Versuch ${next}`;
}
